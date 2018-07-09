from agents.policy_gradients_agent import PolicyGradientsAgentParameters
from graph_managers.basic_rl_graph_manager import BasicRLGraphManager
from graph_managers.graph_manager import ScheduleParameters
from base_parameters import VisualizationParameters
from core_types import TrainingSteps, EnvironmentEpisodes, EnvironmentSteps, RunPhase
from environments.environment import MaxDumpMethod, SelectedPhaseOnlyDumpMethod, SingleLevelSelection
from environments.gym_environment import Mujoco, mujoco_v2, MujocoInputFilter
from exploration_policies.continuous_entropy import ContinuousEntropyParameters
from filters.observation.observation_normalization_filter import ObservationNormalizationFilter
from filters.reward.reward_rescale_filter import RewardRescaleFilter

####################
# Graph Scheduling #
####################
schedule_params = ScheduleParameters()
schedule_params.improve_steps = TrainingSteps(10000000000)
schedule_params.steps_between_evaluation_periods = EnvironmentEpisodes(50)
schedule_params.evaluation_steps = EnvironmentEpisodes(3)
schedule_params.heatup_steps = EnvironmentSteps(0)

#########
# Agent #
#########
agent_params = PolicyGradientsAgentParameters()
agent_params.algorithm.apply_gradients_every_x_episodes = 5
agent_params.algorithm.num_steps_between_gradient_updates = 20000
agent_params.network_wrappers['main'].learning_rate = 0.0005

agent_params.input_filter = MujocoInputFilter()
agent_params.input_filter.add_reward_filter('rescale', RewardRescaleFilter(1/20.))
agent_params.input_filter.add_observation_filter('observation', 'normalize', ObservationNormalizationFilter())


###############
# Environment #
###############
env_params = Mujoco()
env_params.level = "InvertedPendulum-v2"

vis_params = VisualizationParameters()
vis_params.video_dump_methods = [SelectedPhaseOnlyDumpMethod(RunPhase.TEST), MaxDumpMethod()]
vis_params.dump_mp4 = False

graph_manager = BasicRLGraphManager(agent_params=agent_params, env_params=env_params,
                                    schedule_params=schedule_params, vis_params=vis_params)


