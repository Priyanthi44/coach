#
# Copyright (c) 2017 Intel Corporation 
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import copy
from collections import OrderedDict
from random import shuffle
from typing import Union

import numpy as np

from agents.actor_critic_agent import ActorCriticAgent
from agents.policy_optimization_agent import PolicyGradientRescaler
from architectures.tensorflow_components.heads.ppo_head import PPOHeadParameters
from architectures.tensorflow_components.heads.v_head import VHeadParameters
from architectures.tensorflow_components.middlewares.fc_middleware import FCMiddlewareParameters
from base_parameters import AlgorithmParameters, NetworkParameters, \
    AgentParameters, InputEmbedderParameters, DistributedTaskParameters
from core_types import EnvironmentSteps, Batch, EnvResponse, StateType
from exploration_policies.additive_noise import AdditiveNoiseParameters
from logger import screen
from memories.episodic_experience_replay import EpisodicExperienceReplayParameters
from schedules import ConstantSchedule
from spaces import DiscreteActionSpace


class ClippedPPONetworkParameters(NetworkParameters):
    def __init__(self):
        super().__init__()
        self.input_embedders_parameters = {'observation': InputEmbedderParameters(activation_function='tanh')}
        self.middleware_parameters = FCMiddlewareParameters(activation_function='tanh')
        self.heads_parameters = [VHeadParameters(), PPOHeadParameters()]
        self.loss_weights = [1.0, 1.0]
        self.rescale_gradient_from_head_by_factor = [1, 1]
        self.batch_size = 64
        self.optimizer_type = 'Adam'
        self.clip_gradients = None
        self.use_separate_networks_per_head = True
        self.async_training = False
        self.l2_regularization = 0
        self.create_target_network = True
        self.shared_optimizer = True
        self.scale_down_gradients_by_number_of_workers_for_sync_training = True


class ClippedPPOAlgorithmParameters(AlgorithmParameters):
    def __init__(self):
        super().__init__()
        self.num_episodes_in_experience_replay = 1000000
        self.policy_gradient_rescaler = PolicyGradientRescaler.GAE
        self.gae_lambda = 0.95
        self.use_kl_regularization = False
        self.clip_likelihood_ratio_using_epsilon = 0.2
        self.estimate_state_value_using_gae = True
        self.step_until_collecting_full_episodes = True
        self.beta_entropy = 0.01  # should be 0 for mujoco
        self.num_consecutive_playing_steps = EnvironmentSteps(2048)
        self.optimization_epochs = 10
        self.normalization_stats = None
        self.clipping_decay_schedule = ConstantSchedule(1)


class ClippedPPOAgentParameters(AgentParameters):
    def __init__(self):
        super().__init__(algorithm=ClippedPPOAlgorithmParameters(),
                         exploration=AdditiveNoiseParameters(),
                         memory=EpisodicExperienceReplayParameters(),
                         networks={"main": ClippedPPONetworkParameters()})

    @property
    def path(self):
        return 'agents.clipped_ppo_agent:ClippedPPOAgent'


# Clipped Proximal Policy Optimization - https://arxiv.org/abs/1707.06347
class ClippedPPOAgent(ActorCriticAgent):
    def __init__(self, agent_parameters, parent: Union['LevelManager', 'CompositeAgent']=None):
        super().__init__(agent_parameters, parent)
        # signals definition
        self.value_loss = self.register_signal('Value Loss')
        self.policy_loss = self.register_signal('Policy Loss')
        self.total_kl_divergence_during_training_process = 0.0
        self.unclipped_grads = self.register_signal('Grads (unclipped)')
        self.value_targets = self.register_signal('Value Targets')
        self.kl_divergence = self.register_signal('KL Divergence')

    def set_session(self, sess):
        super().set_session(sess)
        if self.ap.algorithm.normalization_stats is not None:
            self.ap.algorithm.normalization_stats.set_session(sess)

    def fill_advantages(self, batch):
        network_keys = self.ap.network_wrappers['main'].input_embedders_parameters.keys()

        current_state_values = self.networks['main'].online_network.predict(batch.states(network_keys))[0]
        current_state_values = current_state_values.squeeze()
        self.state_values.add_sample(current_state_values)

        # calculate advantages
        advantages = []
        value_targets = []
        if self.policy_gradient_rescaler == PolicyGradientRescaler.A_VALUE:
            advantages = batch.total_returns() - current_state_values
        elif self.policy_gradient_rescaler == PolicyGradientRescaler.GAE:
            # get bootstraps
            episode_start_idx = 0
            advantages = np.array([])
            value_targets = np.array([])
            for idx, game_over in enumerate(batch.game_overs()):
                if game_over:
                    # get advantages for the rollout
                    value_bootstrapping = np.zeros((1,))
                    rollout_state_values = np.append(current_state_values[episode_start_idx:idx+1], value_bootstrapping)

                    rollout_advantages, gae_based_value_targets = \
                        self.get_general_advantage_estimation_values(batch.rewards()[episode_start_idx:idx+1],
                                                                     rollout_state_values)
                    episode_start_idx = idx + 1
                    advantages = np.append(advantages, rollout_advantages)
                    value_targets = np.append(value_targets, gae_based_value_targets)
        else:
            screen.warning("WARNING: The requested policy gradient rescaler is not available")

        # standardize
        advantages = (advantages - np.mean(advantages)) / np.std(advantages)

        for transition, advantage, value_target in zip(batch.transitions, advantages, value_targets):
            transition.info['advantage'] = advantage
            transition.info['gae_based_value_target'] = value_target

        self.action_advantages.add_sample(advantages)

    def train_network(self, batch, epochs):
        loss = []
        for j in range(epochs):
            batch.shuffle()
            loss = {
                'total_loss': [],
                'policy_losses': [],
                'unclipped_grads': [],
                'fetch_result': []
            }
            for i in range(int(batch.size / self.ap.network_wrappers['main'].batch_size)):
                start = i * self.ap.network_wrappers['main'].batch_size
                end = (i + 1) * self.ap.network_wrappers['main'].batch_size

                network_keys = self.ap.network_wrappers['main'].input_embedders_parameters.keys()
                actions = batch.actions()[start:end]
                gae_based_value_targets = batch.info('gae_based_value_target')[start:end]
                if not isinstance(self.spaces.action, DiscreteActionSpace) and len(actions.shape) == 1:
                    actions = np.expand_dims(actions, -1)

                # get old policy probabilities and distribution
                result = self.networks['main'].target_network.predict({k: v[start:end] for k, v in batch.states(network_keys).items()})
                old_policy_distribution = result[1:]

                # calculate gradients and apply on both the local policy network and on the global policy network
                fetches = [self.networks['main'].online_network.output_heads[1].kl_divergence,
                           self.networks['main'].online_network.output_heads[1].entropy]

                if self.ap.algorithm.estimate_state_value_using_gae:
                    value_targets = np.expand_dims(gae_based_value_targets, -1)
                else:
                    value_targets = batch.total_returns(expand_dims=True)[start:end]

                inputs = copy.copy({k: v[start:end] for k, v in batch.states(network_keys).items()})
                inputs['output_1_0'] = actions

                # The old_policy_distribution needs to be represented as a list, because in the event of
                # discrete controls, it has just a mean. otherwise, it has both a mean and standard deviation
                for input_index, input in enumerate(old_policy_distribution):
                    inputs['output_1_{}'.format(input_index + 1)] = input

                inputs['output_1_3'] = self.ap.algorithm.clipping_decay_schedule.current_value

                total_loss, policy_losses, unclipped_grads, fetch_result = \
                    self.networks['main'].train_and_sync_networks(
                        inputs, [value_targets, batch.info('advantage')[start:end]], additional_fetches=fetches
                    )

                self.value_targets.add_sample(value_targets)

                loss['total_loss'].append(total_loss)
                loss['policy_losses'].append(policy_losses)
                loss['unclipped_grads'].append(unclipped_grads)
                loss['fetch_result'].append(fetch_result)

                self.unclipped_grads.add_sample(unclipped_grads)

            for key in loss.keys():
                loss[key] = np.mean(loss[key], 0)

            if self.ap.network_wrappers['main'].learning_rate_decay_rate != 0:
                curr_learning_rate = self.networks['main'].online_network.get_variable_value(
                    self.networks['main'].online_network.adaptive_learning_rate_scheme)
                self.curr_learning_rate.add_sample(curr_learning_rate)
            else:
                curr_learning_rate = self.ap.network_wrappers['main'].learning_rate

            # log training parameters
            screen.log_dict(
                OrderedDict([
                    ("Surrogate loss", loss['policy_losses'][0]),
                    ("KL divergence", loss['fetch_result'][0]),
                    ("Entropy", loss['fetch_result'][1]),
                    ("training epoch", j),
                    ("learning_rate", curr_learning_rate)
                ]),
                prefix="Policy training"
            )

        self.total_kl_divergence_during_training_process = loss['fetch_result'][0]
        self.entropy.add_sample(loss['fetch_result'][1])
        self.kl_divergence.add_sample(loss['fetch_result'][0])
        return policy_losses

    def post_training_commands(self):
        # clean memory
        self.call_memory('clean')

    def train(self):
        loss = 0
        if self._should_train(wait_for_full_episode=True):
            dataset = self.memory.transitions
            dataset = self.pre_network_filter.filter(dataset, deep_copy=False)
            batch = Batch(dataset)

            for training_step in range(self.ap.algorithm.num_consecutive_training_steps):
                self.networks['main'].sync()
                self.fill_advantages(batch)

                # take only the requested number of steps
                dataset = dataset[:self.ap.algorithm.num_consecutive_playing_steps.num_steps]
                shuffle(dataset)
                batch = Batch(dataset)

                # update the normalization statistics for all the new observations
                # if self.ap.algorithm.normalization_stats is not None:
                #     self.ap.algorithm.normalization_stats.push(batch.states(['observation'])['observation'])

                losses = self.train_network(batch, self.ap.algorithm.optimization_epochs)

                self.value_loss.add_sample(losses[0])
                self.policy_loss.add_sample(losses[1])
                # TODO: pass the losses to the output of the function

            self.post_training_commands()
            self.training_iteration += 1
            # self.update_log()  # should be done in order to update the data that has been accumulated * while not playing *
            return np.append(losses[0], losses[1])

    def run_pre_network_filter_for_inference(self, state: StateType):
        dummy_env_response = EnvResponse(next_state=state, reward=0, game_over=False)
        return self.pre_network_filter.filter(dummy_env_response, update_internal_state=False)[0].next_state

    def choose_action(self, curr_state):
        self.ap.algorithm.clipping_decay_schedule.step()
        return super().choose_action(curr_state)
