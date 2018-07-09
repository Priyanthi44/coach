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

from typing import Union, List

import numpy as np

from filters.action.box_discretization import BoxDiscretization
from filters.action.partial_discrete_action_space_map import PartialDiscreteActionSpaceMap
from spaces import AttentionActionSpace, BoxActionSpace, DiscreteActionSpace


class AttentionDiscretization(PartialDiscreteActionSpaceMap):
    """
    Given a box action space, this is used to discretize the space.
    The discretization is achieved by creating a grid in the space with num_bins_per_dimension bins per dimension in the
    space. Each discrete action is mapped to a single sub-box in the BoxActionSpace action space.
    """
    def __init__(self, num_bins_per_dimension: Union[int, List[int]], force_int_bins=False):
        # we allow specifying either a single number for all dimensions, or a single number per dimension in the target
        # action space
        self.num_bins_per_dimension = num_bins_per_dimension

        self.force_int_bins = force_int_bins

        # TODO: this will currently only work for attention spaces with 2 dimensions. generalize it.

        super().__init__()

    def validate_output_action_space(self, output_action_space: AttentionActionSpace):
        if not isinstance(output_action_space, AttentionActionSpace):
            raise ValueError("AttentionActionSpace discretization only works with an output space of type AttentionActionSpace. "
                             "The given output space is {}".format(output_action_space))

    def get_unfiltered_action_space(self, output_action_space: AttentionActionSpace) -> DiscreteActionSpace:
        if isinstance(self.num_bins_per_dimension, int):
            self.num_bins_per_dimension = [self.num_bins_per_dimension] * output_action_space.shape[0]

        # create a discrete to linspace map to ease the extraction of attention actions
        discrete_to_box = BoxDiscretization([n+1 for n in self.num_bins_per_dimension],
                                            self.force_int_bins)
        discrete_to_box.get_unfiltered_action_space(BoxActionSpace(output_action_space.shape,
                                                                   output_action_space.low,
                                                                   output_action_space.high), )

        rows, cols = self.num_bins_per_dimension
        start_ind = [i * (cols + 1) + j for i in range(rows + 1) if i < rows for j in range(cols + 1) if j < cols]
        end_ind = [i + cols + 2 for i in start_ind]
        self.target_actions = [np.array([discrete_to_box.target_actions[start],
                                         discrete_to_box.target_actions[end]])
                               for start, end in zip(start_ind, end_ind)]

        return super().get_unfiltered_action_space(output_action_space)
