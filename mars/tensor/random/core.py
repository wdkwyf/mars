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


import itertools
from collections import Iterable, namedtuple
from contextlib import contextmanager

import numpy as np

from ...config import options
from ...compat import irange, izip, zip_longest
from ...serialize import ValueType, TupleField
from ...utils import tokenize
from ..core import TENSOR_TYPE, CHUNK_TYPE
from ..utils import decide_chunk_sizes, random_state_data, broadcast_shape
from ..array_utils import array_module
from ..operands import TensorOperand, TensorOperandMixin
from ..datasource import tensor as astensor
from ..base import broadcast_to


class RandomState(object):
    def __init__(self, seed=None):
        self._random_state = np.random.RandomState(seed=seed)

    def seed(self, seed=None):
        """
        Seed the generator.

        This method is called when `RandomState` is initialized. It can be
        called again to re-seed the generator. For details, see `RandomState`.

        Parameters
        ----------
        seed : int or 1-d array_like, optional
            Seed for `RandomState`.
            Must be convertible to 32 bit unsigned integers.

        See Also
        --------
        RandomState
        """
        self._random_state.seed(seed=seed)

    @property
    def _state(self):
        return State(self._random_state)

    @classmethod
    def from_numpy(cls, np_random_state):
        state = RandomState()
        state._random_state = np_random_state
        return state

    @classmethod
    def _handle_size(cls, size):
        if size is None:
            return size
        try:
            return tuple(size)
        except TypeError:
            return size,


_random_state = RandomState()


def handle_array(arg):
    if not isinstance(arg, TENSOR_TYPE):
        if not isinstance(arg, Iterable):
            return arg

        arg = np.asarray(arg)
        return arg[(0,) * max(1, arg.ndim)]
    elif hasattr(arg, 'op') and hasattr(arg.op, 'data'):
        return arg.op.data[(0,) * max(1, arg.ndim)]

    return np.empty((0,), dtype=arg.dtype)


STATE = namedtuple('State', 'label keys pos has_gauss cached_gaussian')


class State(STATE):
    def __new__(cls, random_state=None, *args, **kwargs):
        if isinstance(random_state, np.random.RandomState) and not kwargs:
            return super(State, cls).__new__(cls, *random_state.get_state())
        elif isinstance(random_state, tuple) and not kwargs:
            return super(State, cls).__new__(cls, *random_state)
        elif args:
            return super(State, cls).__new__(cls, *((random_state,) + args))
        elif not kwargs:
            return super(State, cls).__new__(cls, *random_state)
        else:
            assert random_state is None
            return super(State, cls).__new__(cls, **kwargs)

    def __eq__(self, other):
        if not isinstance(other, tuple):
            return False

        for it, other_it in zip_longest(self, other):
            if isinstance(it, np.ndarray):
                if not np.array_equal(it, other_it):
                    return False
            elif it != other_it:
                return False

        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    @property
    def random_state(self):
        rs = np.random.RandomState()
        s = (self.label, self.keys, self.pos, self.has_gauss, self.cached_gaussian)
        rs.set_state(s)

        return rs


class TensorRandomOperandMixin(TensorOperandMixin):
    __slots__ = ()

    @classmethod
    def tile(cls, op):
        tensor = op.outputs[0]
        chunk_size = tensor.extra_params.raw_chunk_size or options.tensor.chunk_size
        nsplits = decide_chunk_sizes(tensor.shape, chunk_size, tensor.dtype.itemsize)
        fields = getattr(op, '_input_fields_', [])
        to_one_chunk_fields = set(getattr(op, '_into_one_chunk_fields_', list()))

        new_inputs = []
        changed = False
        for field in fields:
            t = getattr(op, field)
            if not isinstance(t, TENSOR_TYPE):
                continue

            if field not in to_one_chunk_fields:
                t_nsplits = nsplits
            else:
                t_nsplits = t.shape  # into 1 chunk
            rechunked = t.rechunk(t_nsplits)
            if rechunked is not t:
                rechunked.single_tiles()
                changed = True
                new_inputs.append(rechunked)
            else:
                new_inputs.append(t)
        if changed:
            op.inputs = new_inputs

        idxes = list(itertools.product(*[irange(len(s)) for s in nsplits]))
        states = random_state_data(len(idxes), op.state.random_state) \
            if op.state is not None else [None] * len(idxes)

        out_chunks = []
        for state, idx, shape in izip(states, idxes, itertools.product(*nsplits)):
            inputs = []
            for inp in op.inputs:
                if len(inp.chunks) == 1:
                    inputs.append(inp.chunks[0])
                else:
                    inputs.append(inp.cix[idx])
            state = State(np.random.RandomState(state)) \
                if state is not None else None
            try:
                s = len(tuple(op.size))
                size = shape[:s]
            except TypeError:
                if op.size is None:
                    size = None
                else:
                    size = shape[:1]
            except AttributeError:
                size = shape

            chunk_op = op.copy().reset_key()
            chunk_op._size = size
            chunk_op._state = state
            out_chunk = chunk_op.new_chunk(inputs, shape=shape, index=idx)
            out_chunks.append(out_chunk)

        new_op = op.copy()
        return new_op.new_tensors(op.inputs, tensor.shape,
                                  chunks=out_chunks, nsplits=nsplits)

    @classmethod
    def execute(cls, ctx, op):
        xp = array_module(op.gpu)
        if op.state:
            rs = op.state.random_state
        else:
            if xp == np:
                rs = xp.random.RandomState()
            else:
                rs = xp.random
        get_val = lambda x: ctx[x.key] if isinstance(x, CHUNK_TYPE) else x

        method_name = getattr(cls, '_func_name')
        try:
            if method_name in ('rand', 'randn'):
                try:
                    res = getattr(rs, method_name)(*op.size, dtype=op.dtype)
                except TypeError:
                    res = getattr(rs, method_name)(*op.size)
            elif method_name == 'randint':
                try:
                    res = rs.randint(get_val(op.low), get_val(op.high), size=op.size,
                                     dtype=op.dtype)
                except TypeError:
                    res = rs.randint(get_val(op.low), get_val(op.high), size=op.size)
            else:
                try:
                    res = getattr(rs, method_name)(*(get_val(getattr(op, arg)) for arg in op.args),
                                                   dtype=op.dtype)
                except TypeError:
                    res = getattr(rs, method_name)(*(get_val(getattr(op, arg)) for arg in op.args))
            if hasattr(res, 'dtype') and res.dtype != op.dtype:
                res = res.astype(op.dtype)
            if xp != np:
                with xp.cuda.Device(op.device or 0):
                    ctx[op.outputs[0].key] = xp.asarray(res)
            else:
                ctx[op.outputs[0].key] = res
        except AttributeError:
            if xp != np:
                if not op.state:
                    rs = np.random.RandomState()
                if method_name in ('rand', 'randn'):
                    try:
                        res = getattr(rs, method_name)(*op.size, dtype=op.dtype)
                    except TypeError:
                        res = getattr(rs, method_name)(*op.size)
                else:
                    try:
                        res = getattr(rs, method_name)(*(get_val(getattr(op, arg)) for arg in op.args),
                                                       dtype=op.dtype)
                    except TypeError:
                        res = getattr(rs, method_name)(*(get_val(getattr(op, arg)) for arg in op.args))
                if res.dtype != op.dtype:
                    res = res.astype(op.dtype)
                with xp.cuda.Device(op.device or 0):
                    ctx[op.outputs[0].key] = xp.asarray(res)
            else:
                raise

    def _get_shape(self, shapes):
        shapes = list(shapes)
        if getattr(self, '_size', None) is not None:
            shapes.append(getattr(self, '_size'))
        return broadcast_shape(*shapes)

    @classmethod
    def _handle_arg(cls, arg, chunk_size):
        if isinstance(arg, (list, np.ndarray)):
            arg = astensor(arg, chunk_size=chunk_size)

        return arg

    @contextmanager
    def _get_inputs_shape_by_given_fields(self, inputs, shape, raw_chunk_size=None, tensor=True):
        fields = getattr(self, '_input_fields_', [])
        to_one_chunk_fields = set(getattr(self, '_into_one_chunk_fields_', list()))

        field_to_obj = dict()
        to_broadcast_shapes = []
        if fields:
            if getattr(self, fields[0], None) is None:
                # create from beginning
                for field, val in zip(fields, inputs):
                    if field not in to_one_chunk_fields:
                        if isinstance(val, list):
                            val = np.asarray(val)
                        if tensor:
                            val = self._handle_arg(val, raw_chunk_size)
                    if isinstance(val, TENSOR_TYPE + CHUNK_TYPE):
                        field_to_obj[field] = val
                        if field not in to_one_chunk_fields:
                            to_broadcast_shapes.append(val.shape)
                    setattr(self, field, val)
            else:
                inputs_iter = iter(inputs)
                for field in fields:
                    if isinstance(getattr(self, field), TENSOR_TYPE + CHUNK_TYPE):
                        field_to_obj[field] = next(inputs_iter)

        if tensor:
            if shape is None:
                shape = self._get_shape(to_broadcast_shapes)

            for field, inp in field_to_obj.items():
                if field not in to_one_chunk_fields:
                    field_to_obj[field] = broadcast_to(inp, shape)

        yield [field_to_obj[f] for f in fields if f in field_to_obj], shape

        inputs_iter = iter(getattr(self, '_inputs'))
        for field in fields:
            if field in field_to_obj:
                setattr(self, field, next(inputs_iter))

    def _new_tileables(self, inputs, kws=None, **kw):
        raw_chunk_size = kw.get('chunk_size', None)
        shape = kw.get('shape', None)
        with self._get_inputs_shape_by_given_fields(inputs, shape, raw_chunk_size, True) as (inputs, shape):
            kw['shape'] = shape
            return super(TensorRandomOperandMixin, self)._new_tileables(inputs, kws=kws, **kw)

    def _new_chunks(self, inputs, kws=None, **kw):
        shape = kw.get('shape', None)
        with self._get_inputs_shape_by_given_fields(inputs, shape, None, False) as (inputs, shape):
            kw['shape'] = shape
            return super(TensorRandomOperandMixin, self)._new_chunks(inputs, kws=kws, **kw)


class TensorRandomOperand(TensorOperand):
    _state = TupleField('state', on_serialize=lambda x: tuple(x) if x is not None else x,
                        on_deserialize=lambda x: State(x) if x is not None else x)

    @property
    def state(self):
        return getattr(self, '_state', None)

    def __setattr__(self, attr, value):
        if attr == '_state' and value is not None and not isinstance(value, State):
            value = State(value)
        super(TensorRandomOperand, self).__setattr__(attr, value)

    @property
    def args(self):
        return [slot for slot in self.__slots__
                if slot not in set(TensorRandomOperand.__slots__)]

    def _update_key(self):
        args = tuple(getattr(self, k, None) for k in self._keys_)
        if self.state is None:
            args += (np.random.random(),)
        self._key = tokenize(type(self), *args)


class TensorDistribution(TensorRandomOperand):
    _size = TupleField('size', ValueType.int64)

    @property
    def size(self):
        return self._size

    @classmethod
    def execute(cls, ctx, op):
        xp = array_module(op.gpu)
        if op.state:
            rs = op.state.random_state
        else:
            rs = xp.random.RandomState()

        args = []
        for k in op.args:
            val = getattr(op, k, None)
            if isinstance(val, CHUNK_TYPE):
                args.append(ctx[val.key])
            else:
                args.append(val)

        method_name = getattr(cls, '_func_name')
        try:
            res = getattr(rs, method_name)(*args)
            if xp != np:
                with xp.cuda.Device(op.device or 0):
                    ctx[op.outputs[0].key] = xp.asarray(res)
            else:
                ctx[op.outputs[0].key] = res
        except AttributeError:
            if xp != np:
                if not op.state:
                    rs = np.random.RandomState()
                res = getattr(rs, method_name)(*args)
                with xp.cuda.Device(op.device or 0):
                    ctx[op.outputs[0].key] = xp.asarray(res)
            else:
                raise


class TensorSimpleRandomData(TensorRandomOperand):
    _size = TupleField('size', ValueType.int64)

    @property
    def size(self):
        return self._size
