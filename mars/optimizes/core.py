#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 1999-2018 Alibaba Group Holding Ltd.
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

from ..tensor.fuse.ne import NUMEXPR_INSTALLED
from ..tensor.fuse.jax import JAX_INSTALLED
from .ne import NeOptimizer
from .cp import CpOptimizer
from .jax import JaxOptimizer


class Optimizer(object):
    engine_dic = {'numexpr': NeOptimizer,
                  'cupy': CpOptimizer, 'jax': JaxOptimizer}

    def __init__(self, graph, engine=None):
        self._graph = graph

        if not engine:
            # the sequence of optimize
            if JAX_INSTALLED:
                self._engine = 'jax'
            elif NUMEXPR_INSTALLED:
                self._engine = 'numexpr'
            else:
                self._engine = 'numpy'
        else:
            self._engine = engine

    def optimize(self, keys=None):
        self._graph.decompose()
        if self._engine == 'numpy':
            return
        optimizer = self.engine_dic[self._engine](self._graph)
        optimizer.optimize(keys=keys)
