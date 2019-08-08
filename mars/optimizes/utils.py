from .ne import SUPPORT_OP, REDUCTION_OP, NeOptimizer
from .jax import JaxOptimizer


def check_reduction_axis(node):
    return len(node.op.axis) == 1 or len(node.op.axis) == node.ndim


class Composer:
    def __init__(self, optimizer, keys):
        if isinstance(optimizer, NeOptimizer):
            self.engine = 'numexpr'
        if isinstance(optimizer, JaxOptimizer):
            self.engine = 'jax'
        self.explored = set()
        self.keys = set(keys or [])
        self.graph = optimizer.graph

    def _can_skip(self, node):
        op = node.op

        if self.engine == 'numexpr':
            if not isinstance(op, SUPPORT_OP) or node.key in self.keys:
                return True
            if node in self.explored or isinstance(op, REDUCTION_OP):
                return True
            if self.graph.count_successors(node) != 1:
                return True

        if self.engine == 'jax':
            if not hasattr(op, 'execute_jax') or node.key in self.keys:
                return True
            if node in self.explored:
                return True
            if self.graph.count_successors(node) != 1:
                return True

        return False

    def _can_break(self, node):
        if self.engine == 'numexpr':
            if self.graph.count_successors(node) != 1 and isinstance(node.op, REDUCTION_OP):
                return True
        if self.engine == 'jax':
            if self.graph.count_successors(node) != 1:
                return True
        return False

    def _support(self, node):
        op_type = type(node.op)

        if self.engine == 'numexpr':
            if op_type in REDUCTION_OP:
                return check_reduction_axis(node)
            return op_type in SUPPORT_OP
        if self.engine == 'jax':
            return hasattr(node.op, 'execute_jax')

    def _get_fused_chunk(self, tail_node):
        if self.engine == 'numexpr':
            from ..tensor.fuse import TensorNeFuseChunk
            return TensorNeFuseChunk(dtype=tail_node.dtype)

        if self.engine == 'jax':
            from ..tensor.fuse import TensorJaxFuseChunk
            return TensorJaxFuseChunk(dtype=tail_node.dtype)

    def compose(self):
        composes = []

        graph = self.graph
        for v in graph.bfs():
            if v.op.gpu or v.op.sparse:
                # break out
                return []
            if self._can_skip(v):
                continue
            selected = [v]
            # add successors
            cur_node = graph.successors(v)[0]
            while graph.count_predecessors(cur_node) == 1 \
                    and self._support(cur_node) and cur_node.key not in self.keys:
                selected.append(cur_node)
                if self._can_break(v):
                    break
                else:
                    cur_node = graph.successors(cur_node)[0]
            if len(selected) > 1:
                self.explored.update(selected)
                composes.append(list(selected))

        # compose to fused node
        composed_nodes = []

        for c in composes:
            head_node = c[0]
            tail_node = c[-1]

            op = self._get_fused_chunk(tail_node)
            composed_chunk = op(c).data
            graph.add_node(composed_chunk)
            for node in graph.iter_successors(tail_node):
                graph.add_edge(composed_chunk, node)
            for node in graph.iter_predecessors(head_node):
                graph.add_edge(node, composed_chunk)
            for node in c:
                graph.remove_node(node)
            composed_nodes.append(composed_chunk)

        return composed_nodes
