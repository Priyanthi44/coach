from configurations import *
from block_factories.basic_rl_factory import BasicRLFactory


class AgentParams(AgentParameters):
    def __init__(self):
        AgentParameters.__init__(self, DFP, ExplorationParameters, None)
        pass
        self.algorithm.num_episodes_in_experience_replay = 200
        self.learning_rate = 0.00025
        self.num_heatup_steps = 1000
        self.exploration.epsilon_decay_steps = 10000
        self.algorithm.use_accumulated_reward_as_measurement = True


class EnvParams(Doom):
    def __init__(self):
        super().__init__()
        self.level = 'HEALTH_GATHERING'


class VisParams(VisualizationParameters):
    def __init__(self):
        super().__init__()

factory = BasicRLFactory(agent_params=AgentParams, env_params=EnvParams, vis_params=VisParams)