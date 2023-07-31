# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import logging
from typing import final, List

import torch
from executorch import exir
from executorch.backends.canonical_partitioners.pattern_op_partitioner import (
    generate_pattern_op_partitions,
)

from executorch.backends.partitioner import DelegationSpec, Partitioner
from executorch.backends.test.qnn_backend_demo import QnnBackend
from torch.fx import GraphModule
from torch.fx.passes.infra.partitioner import Partition


@final
class HTAPartitionerMultiplePatternsDemo(Partitioner):
    """
    An example implementation to partition graph for HTA, in this example, the backend
    associate with this partitioner is QnnBackend. With QnnBackend, the two lowerable
    patterns are: (lstm + conv) and (sub). backend is a class member instead of instance
    members, as it is a properties of HTAPartitionerMultiplePatternsDemo, and won't be different for
    different HTAPartitionerMultiplePatternsDemo instances.

    The partition algorithm is:
    1. Find out a list of partitions given a graph: generate_partition_list(GraphModule) -> List[Partition]:
    2. Check if all partitions from generate_partition_list() are exclusive. If they are, it will error out
    3. Fuse the partition list as submodules.
    """

    def __init__(self) -> None:
        """
        Initialize a list of pattern partitioners: (lstm + conv) and (sub)
        """

        class LSTMConvPattern(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = torch.nn.LSTM(
                    input_size=32,
                    hidden_size=32,
                    num_layers=1,
                )
                self.conv = torch.nn.Conv1d(1, 1, 1, stride=2)

            def forward(self, x_raw, h, c):
                output, (hn, cn) = self.lstm(x_raw, (h, c))
                k = self.conv(output)
                return output, hn, cn, k

        input_x = torch.ones([1, 32])
        input_h = torch.ones([1, 32])
        input_c = torch.ones([1, 32])

        pattern_lstm_conv = (
            exir.capture(
                LSTMConvPattern(),
                (input_x, input_h, input_c),
                exir.CaptureConfig(pt2_mode=True),
            )
            .to_edge(exir.EdgeCompileConfig(_check_ir_validity=False))
            .exported_program.graph_module
        )

        def sub(x, y):
            return torch.sub(x, y)

        pattern_sub = (
            exir.capture(
                sub,
                (input_x, input_h),
                exir.CaptureConfig(pt2_mode=True),
            )
            .to_edge()
            .exported_program.graph_module
        )
        self.patterns = [pattern_lstm_conv.graph, pattern_sub.graph]

        backend_id = QnnBackend.__name__
        self.delegation_spec = DelegationSpec(backend_id, [])

        self.partition_tags = {}

    def is_exclusive(self, partition_list_list: List[List[Partition]]) -> bool:
        """
        List[Partition] is generate from one pattern partitioner, and this partitioner
        only supports merging exclusive partitions. It will check if all partitions are
        exclusive by comparing len(all_nodes) and len(set(all_nodes))

        Args:
            partition_list_list: all partitions from all pattern partitioners

        Returns:
            bool: True if all nodes from all partitions are exclusive.

        For example, 0/1 are the partition id, A/B/../L are nodes:
        [
            [(0: A, B, C), (1: D, E, F)], # from pattern lstm + conv
            [(0: B, J, L)], # from sub
        ]
        node B shows up in both partition. Usually some special tricks (either merge two list,
        or only keep one pattern [A, B, C]) needs to done here, depending on user's need.
        """
        all_partition = [
            partition
            for partition_list in partition_list_list
            for partition in partition_list
        ]

        # All nodes from all partitions from all pattern match results
        all_nodes = []
        for partition in all_partition:
            all_nodes.extend(partition.nodes)
        all_nodes_set = set(all_nodes)

        # Calculate the number of duplciate nodes
        duplicated_node_number = len(all_nodes) - len(all_nodes_set)
        logging.info(f"duplicate node number is {duplicated_node_number}.")
        return duplicated_node_number == 0

    def generate_partition_list(self, graph_module) -> List[Partition]:
        """
        Generate a list of partitions from all matched patterns

        Args:
            graph_module: the input graph module

        Returns:
            bool: True if all partitions are exclusive.

        For example, 0/1 are the partition id, A/B/../L are nodes:
        [
            [(0: A, B, C), (1: D, E, F)], # from pattern lstm + conv
            [(0: G, H, I)], # from sub
        ]
        the output will be
        [
            [(0: A, B, C), (1: D, E, F), (3: G, H, I)]
        ]

        """
        partitions_from_all_pattern = [
            generate_pattern_op_partitions(graph_module, patterns=[pattern])
            for pattern in self.patterns
        ]

        # Check if all partitions are exclusive, this partitions don't support inclusive partitions.
        is_exclusive = self.is_exclusive(partitions_from_all_pattern)

        assert (
            is_exclusive
        ), "There exists inclusive partitions. Currently the fuse method only handle exclusive partitions."

        # Assign a unique id for each partition
        partition_id = 0

        # If want to support inclusive partitions, the logic can be done here to merge partitions etc.
        flat_proposed_partitions_with_unique_id = []
        for partitions_from_one_pattern in partitions_from_all_pattern:
            for partition in partitions_from_one_pattern:
                partition.id = partition_id
                flat_proposed_partitions_with_unique_id.append(partition)
                partition_id += 1

        return flat_proposed_partitions_with_unique_id

    def partition(self, graph_module: GraphModule) -> GraphModule:
        partition_list = self.generate_partition_list(graph_module)
        for partition in partition_list:
            for node in partition.nodes:
                delegation_tag = f"tag{partition.id}"
                node.meta["delegation_tag"] = delegation_tag
                self.partition_tags[delegation_tag] = self.delegation_spec
        return graph_module


@final
class HTAPartitionerOnePatternDemo(Partitioner):
    """
    Similar to HTAPartitionerMultiplePatternDemo, the only difference is only one pattern (lstm + conv)
    is lowerable. We can subclass PatternPartitioner and use the PatternPartitioner.generate_submodules()
    function to get the graph with submodules and tag accordingly.
    """

    def __init__(self) -> None:
        """
        Initialize the parent class PatternPartitioner with the pattern (lstm + conv)
        """

        # Only lowering lstm + conv pattern
        class LSTMConvPattern(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = torch.nn.LSTM(
                    input_size=32,
                    hidden_size=32,
                    num_layers=1,
                )
                self.conv = torch.nn.Conv1d(1, 1, 1, stride=2)

            def forward(self, x_raw, h, c):
                output, (hn, cn) = self.lstm(x_raw, (h, c))
                k = self.conv(output)
                return output, hn, cn, k

        input_x = torch.ones([1, 32])
        input_h = torch.ones([1, 32])
        input_c = torch.ones([1, 32])

        pattern_lstm_conv = (
            exir.capture(
                LSTMConvPattern(),
                (input_x, input_h, input_c),
                exir.CaptureConfig(pt2_mode=True, enable_aot=True, _unlift=False),
            )
            .to_edge(exir.EdgeCompileConfig(_check_ir_validity=False))
            .exported_program.graph_module
        )
        self.patterns = [pattern_lstm_conv.graph]
        # Only (lstm + conv) pattern is lowerable

        backend_id = QnnBackend.__name__
        self.delegation_spec = DelegationSpec(backend_id, [])

        self.partition_tags = {}

    def partition(
        self, edge_graph_module: torch.fx.GraphModule
    ) -> torch.fx.GraphModule:
        partition_list = generate_pattern_op_partitions(
            edge_graph_module, patterns=self.patterns
        )
        for partition in partition_list:
            for node in partition.nodes:
                delegation_tag = f"tag{partition.id}"
                node.meta["delegation_tag"] = delegation_tag
                self.partition_tags[delegation_tag] = self.delegation_spec
        return edge_graph_module
