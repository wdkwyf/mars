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

import unittest
from weakref import ReferenceType
import mars.tensor as mt
import numpy as np

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None

from mars import opcodes as OperandDef
from mars.graph import DAG
from mars.tests.core import TestBase
from mars.dataframe.core import IndexValue, DataFrameChunk
from mars.dataframe.datasource.dataframe import from_pandas as from_pandas_df
from mars.dataframe.datasource.series import from_pandas as from_pandas_series
from mars.dataframe.datasource.from_tensor import from_tensor
from mars.dataframe.datasource.from_records import from_records


@unittest.skipIf(pd is None, 'pandas not installed')
class Test(TestBase):
    def testChunkSerialize(self):
        data = pd.DataFrame(np.random.rand(10, 10), index=np.random.randint(-100, 100, size=(10,)),
                            columns=[np.random.bytes(10) for _ in range(10)])
        df = from_pandas_df(data).tiles()

        # pb
        chunk = df.chunks[0]
        serials = self._pb_serial(chunk)
        op, pb = serials[chunk.op, chunk.data]

        self.assertEqual(tuple(pb.index), chunk.index)
        self.assertEqual(pb.key, chunk.key)
        self.assertEqual(tuple(pb.shape), chunk.shape)
        self.assertEqual(int(op.type.split('.', 1)[1]), OperandDef.DATAFRAME_DATA_SOURCE)

        chunk2 = self._pb_deserial(serials)[chunk.data]

        self.assertEqual(chunk.index, chunk2.index)
        self.assertEqual(chunk.key, chunk2.key)
        self.assertEqual(chunk.shape, chunk2.shape)
        pd.testing.assert_index_equal(chunk2.index_value.to_pandas(), chunk.index_value.to_pandas())
        pd.testing.assert_index_equal(chunk2.columns.to_pandas(), chunk.columns.to_pandas())

        # json
        chunk = df.chunks[0]
        serials = self._json_serial(chunk)

        chunk2 = self._json_deserial(serials)[chunk.data]

        self.assertEqual(chunk.index, chunk2.index)
        self.assertEqual(chunk.key, chunk2.key)
        self.assertEqual(chunk.shape, chunk2.shape)
        pd.testing.assert_index_equal(chunk2.index_value.to_pandas(), chunk.index_value.to_pandas())
        pd.testing.assert_index_equal(chunk2.columns.to_pandas(), chunk.columns.to_pandas())

    def testDataFrameGraphSerialize(self):
        df = from_pandas_df(pd.DataFrame(np.random.rand(10, 10),
                                         columns=[np.random.bytes(10) for _ in range(10)]))
        graph = df.build_graph(tiled=False)

        pb = graph.to_pb()
        graph2 = DAG.from_pb(pb)
        self.assertEqual(len(graph), len(graph2))
        t = next(iter(graph))
        t2 = next(iter(graph2))
        self.assertTrue(t2.op.outputs[0], ReferenceType)  # make sure outputs are all weak reference
        self.assertBaseEqual(t.op, t2.op)
        self.assertEqual(t.shape, t2.shape)
        self.assertEqual(sorted(i.key for i in t.inputs), sorted(i.key for i in t2.inputs))
        pd.testing.assert_index_equal(t2.index_value.to_pandas(), t.index_value.to_pandas())
        pd.testing.assert_index_equal(t2.columns.to_pandas(), t.columns.to_pandas())

        jsn = graph.to_json()
        graph2 = DAG.from_json(jsn)
        self.assertEqual(len(graph), len(graph2))
        t = next(iter(graph))
        t2 = next(iter(graph2))
        self.assertTrue(t2.op.outputs[0], ReferenceType)  # make sure outputs are all weak reference
        self.assertBaseEqual(t.op, t2.op)
        self.assertEqual(t.shape, t2.shape)
        self.assertEqual(sorted(i.key for i in t.inputs), sorted(i.key for i in t2.inputs))
        pd.testing.assert_index_equal(t2.index_value.to_pandas(), t.index_value.to_pandas())
        pd.testing.assert_index_equal(t2.columns.to_pandas(), t.columns.to_pandas())

        # test graph with tiled DataFrame
        t2 = from_pandas_df(pd.DataFrame(np.random.rand(10, 10)), chunk_size=(5, 4)).tiles()
        graph = DAG()
        graph.add_node(t2)

        pb = graph.to_pb()
        graph2 = DAG.from_pb(pb)
        self.assertEqual(len(graph), len(graph2))
        chunks = next(iter(graph2)).chunks
        self.assertEqual(len(chunks), 6)
        self.assertIsInstance(chunks[0], DataFrameChunk)
        self.assertEqual(chunks[0].index, t2.chunks[0].index)
        self.assertBaseEqual(chunks[0].op, t2.chunks[0].op)
        pd.testing.assert_index_equal(chunks[0].index_value.to_pandas(), t2.chunks[0].index_value.to_pandas())
        pd.testing.assert_index_equal(chunks[0].columns.to_pandas(), t2.chunks[0].columns.to_pandas())

        jsn = graph.to_json()
        graph2 = DAG.from_json(jsn)
        self.assertEqual(len(graph), len(graph2))
        chunks = next(iter(graph2)).chunks
        self.assertEqual(len(chunks), 6)
        self.assertIsInstance(chunks[0], DataFrameChunk)
        self.assertEqual(chunks[0].index, t2.chunks[0].index)
        self.assertBaseEqual(chunks[0].op, t2.chunks[0].op)
        pd.testing.assert_index_equal(chunks[0].index_value.to_pandas(), t2.chunks[0].index_value.to_pandas())
        pd.testing.assert_index_equal(chunks[0].columns.to_pandas(), t2.chunks[0].columns.to_pandas())

    def testFromPandasDataFrame(self):
        data = pd.DataFrame(np.random.rand(10, 10), columns=['c' + str(i) for i in range(10)])
        df = from_pandas_df(data, chunk_size=4)

        pd.testing.assert_series_equal(df.op.dtypes, data.dtypes)
        self.assertIsInstance(df.index_value._index_value, IndexValue.RangeIndex)
        self.assertEqual(df.index_value._index_value._slice, slice(0, 10, 1))
        self.assertTrue(df.index_value.is_monotonic_increasing)
        self.assertFalse(df.index_value.is_monotonic_decreasing)
        self.assertTrue(df.index_value.is_unique)
        self.assertEqual(df.index_value.min_val, 0)
        self.assertEqual(df.index_value.max_val, 9)
        np.testing.assert_equal(df.columns._index_value._data, data.columns.values)

        df.tiles()

        self.assertEqual(len(df.chunks), 9)
        pd.testing.assert_frame_equal(df.chunks[0].op.data, df.op.data.iloc[:4, :4])
        self.assertEqual(df.chunks[0].index_value._index_value._slice, slice(0, 4, 1))
        self.assertTrue(df.chunks[0].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(df.chunks[0].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(df.chunks[0].index_value._index_value._is_unique)
        pd.testing.assert_frame_equal(df.chunks[1].op.data, df.op.data.iloc[:4, 4:8])
        self.assertEqual(df.chunks[1].index_value._index_value._slice, slice(0, 4, 1))
        self.assertTrue(df.chunks[1].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(df.chunks[1].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(df.chunks[1].index_value._index_value._is_unique)
        pd.testing.assert_frame_equal(df.chunks[2].op.data, df.op.data.iloc[:4, 8:])
        self.assertEqual(df.chunks[2].index_value._index_value._slice, slice(0, 4, 1))
        self.assertTrue(df.chunks[2].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(df.chunks[2].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(df.chunks[2].index_value._index_value._is_unique)
        pd.testing.assert_frame_equal(df.chunks[3].op.data, df.op.data.iloc[4:8, :4])
        self.assertEqual(df.chunks[3].index_value._index_value._slice, slice(4, 8, 1))
        self.assertTrue(df.chunks[3].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(df.chunks[3].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(df.chunks[3].index_value._index_value._is_unique)
        pd.testing.assert_frame_equal(df.chunks[4].op.data, df.op.data.iloc[4:8, 4:8])
        self.assertEqual(df.chunks[4].index_value._index_value._slice, slice(4, 8, 1))
        self.assertTrue(df.chunks[4].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(df.chunks[4].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(df.chunks[4].index_value._index_value._is_unique)
        pd.testing.assert_frame_equal(df.chunks[5].op.data, df.op.data.iloc[4:8, 8:])
        self.assertEqual(df.chunks[5].index_value._index_value._slice, slice(4, 8, 1))
        self.assertTrue(df.chunks[5].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(df.chunks[5].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(df.chunks[5].index_value._index_value._is_unique)
        pd.testing.assert_frame_equal(df.chunks[6].op.data, df.op.data.iloc[8:, :4])
        self.assertEqual(df.chunks[6].index_value._index_value._slice, slice(8, 10, 1))
        self.assertTrue(df.chunks[6].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(df.chunks[6].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(df.chunks[6].index_value._index_value._is_unique)
        pd.testing.assert_frame_equal(df.chunks[7].op.data, df.op.data.iloc[8:, 4:8])
        self.assertEqual(df.chunks[7].index_value._index_value._slice, slice(8, 10, 1))
        self.assertTrue(df.chunks[7].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(df.chunks[7].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(df.chunks[7].index_value._index_value._is_unique)
        pd.testing.assert_frame_equal(df.chunks[8].op.data, df.op.data.iloc[8:, 8:])
        self.assertEqual(df.chunks[8].index_value._index_value._slice, slice(8, 10, 1))
        self.assertTrue(df.chunks[8].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(df.chunks[8].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(df.chunks[8].index_value._index_value._is_unique)

        data2 = data[::2]
        df2 = from_pandas_df(data2, chunk_size=4)

        pd.testing.assert_series_equal(df.op.dtypes, data2.dtypes)
        self.assertIsInstance(df2.index_value._index_value, IndexValue.RangeIndex)
        self.assertEqual(df2.index_value._index_value._slice, slice(0, 10, 2))

        df2.tiles()

        self.assertEqual(len(df2.chunks), 6)
        pd.testing.assert_frame_equal(df2.chunks[0].op.data, df2.op.data.iloc[:4, :4])
        self.assertEqual(df2.chunks[0].index_value._index_value._slice, slice(0, 8, 2))
        pd.testing.assert_frame_equal(df2.chunks[1].op.data, df2.op.data.iloc[:4, 4:8])
        self.assertEqual(df2.chunks[1].index_value._index_value._slice, slice(0, 8, 2))
        pd.testing.assert_frame_equal(df2.chunks[2].op.data, df2.op.data.iloc[:4, 8:])
        self.assertEqual(df2.chunks[2].index_value._index_value._slice, slice(0, 8, 2))
        pd.testing.assert_frame_equal(df2.chunks[3].op.data, df2.op.data.iloc[4:, :4])
        self.assertEqual(df2.chunks[3].index_value._index_value._slice, slice(8, 10, 2))
        pd.testing.assert_frame_equal(df2.chunks[4].op.data, df2.op.data.iloc[4:, 4:8])
        self.assertEqual(df2.chunks[3].index_value._index_value._slice, slice(8, 10, 2))
        pd.testing.assert_frame_equal(df2.chunks[5].op.data, df2.op.data.iloc[4:, 8:])
        self.assertEqual(df2.chunks[3].index_value._index_value._slice, slice(8, 10, 2))

    def testFromPandasSeries(self):
        data = pd.Series(np.random.rand(10), name='a')
        series = from_pandas_series(data, chunk_size=4)

        self.assertEqual(series.name, data.name)
        self.assertIsInstance(series.index_value._index_value, IndexValue.RangeIndex)
        self.assertEqual(series.index_value._index_value._slice, slice(0, 10, 1))
        self.assertTrue(series.index_value.is_monotonic_increasing)
        self.assertFalse(series.index_value.is_monotonic_decreasing)
        self.assertTrue(series.index_value.is_unique)
        self.assertEqual(series.index_value.min_val, 0)
        self.assertEqual(series.index_value.max_val, 9)

        series.tiles()

        self.assertEqual(len(series.chunks), 3)
        pd.testing.assert_series_equal(series.chunks[0].op.data, series.op.data.iloc[:4])
        self.assertEqual(series.chunks[0].index_value._index_value._slice, slice(0, 4, 1))
        self.assertTrue(series.chunks[0].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(series.chunks[0].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(series.chunks[0].index_value._index_value._is_unique)
        pd.testing.assert_series_equal(series.chunks[1].op.data, series.op.data.iloc[4:8])
        self.assertEqual(series.chunks[1].index_value._index_value._slice, slice(4, 8, 1))
        self.assertTrue(series.chunks[1].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(series.chunks[1].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(series.chunks[1].index_value._index_value._is_unique)
        pd.testing.assert_series_equal(series.chunks[2].op.data, series.op.data.iloc[8:])
        self.assertEqual(series.chunks[2].index_value._index_value._slice, slice(8, 10, 1))
        self.assertTrue(series.chunks[2].index_value._index_value._is_monotonic_increasing)
        self.assertFalse(series.chunks[2].index_value._index_value._is_monotonic_decreasing)
        self.assertTrue(series.chunks[2].index_value._index_value._is_unique)

    def testFromTensorSerialize(self):
        # test serialization and deserialization
        # pb
        tensor = mt.random.rand(10, 10)
        df = from_tensor(tensor)
        df.tiles()
        chunk = df.chunks[0]
        serials = self._pb_serial(chunk)
        op, pb = serials[chunk.op, chunk.data]

        self.assertEqual(tuple(pb.index), chunk.index)
        self.assertEqual(pb.key, chunk.key)
        self.assertEqual(tuple(pb.shape), chunk.shape)
        self.assertEqual(int(op.type.split('.', 1)[1]), OperandDef.DATAFRAME_FROM_TENSOR)

        chunk2 = self._pb_deserial(serials)[chunk.data]

        self.assertEqual(chunk.index, chunk2.index)
        self.assertEqual(chunk.key, chunk2.key)
        self.assertEqual(chunk.shape, chunk2.shape)
        pd.testing.assert_index_equal(chunk2.index_value.to_pandas(), chunk.index_value.to_pandas())
        pd.testing.assert_index_equal(chunk2.columns.to_pandas(), chunk.columns.to_pandas())

        # json
        chunk = df.chunks[0]
        serials = self._json_serial(chunk)

        chunk2 = self._json_deserial(serials)[chunk.data]

        self.assertEqual(chunk.index, chunk2.index)
        self.assertEqual(chunk.key, chunk2.key)
        self.assertEqual(chunk.shape, chunk2.shape)
        pd.testing.assert_index_equal(chunk2.index_value.to_pandas(), chunk.index_value.to_pandas())
        pd.testing.assert_index_equal(chunk2.columns.to_pandas(), chunk.columns.to_pandas())

    def testFromTensor(self):
        tensor = mt.random.rand(10, 10, chunk_size=5)
        df = from_tensor(tensor)
        self.assertIsInstance(df.index_value._index_value, IndexValue.RangeIndex)
        self.assertEqual(df.op.dtypes[0], tensor.dtype, 'DataFrame converted from tensor have the wrong dtype')

        df.tiles()
        self.assertEqual(len(df.chunks), 4)
        self.assertIsInstance(df.chunks[0].index_value._index_value, IndexValue.RangeIndex)
        self.assertIsInstance(df.chunks[0].index_value, IndexValue)

        # test converted from 1-d tensor
        tensor2 = mt.array([1, 2, 3])
        # in fact, tensor3 is (3,1)
        tensor3 = mt.array([tensor2]).T

        df2 = from_tensor(tensor2)
        df3 = from_tensor(tensor3)
        df2.tiles()
        df3.tiles()
        np.testing.assert_equal(df2.chunks[0].index, (0, 0))
        np.testing.assert_equal(df3.chunks[0].index, (0, 0))

        # test converted from scalar
        scalar = mt.array(1)
        np.testing.assert_equal(scalar.ndim, 0)
        with self.assertRaises(TypeError):
            from_tensor(scalar)

    def testFromRecords(self):
        dtype = np.dtype([('x', 'int'), ('y', 'double'), ('z', '<U16')])

        tensor = mt.ones((10,), dtype=dtype, chunk_size=3)
        df = from_records(tensor)
        df.tiles()

        self.assertEqual(df.chunk_shape, (4, 1))
        self.assertEqual(df.chunks[0].shape, (3, 3))
        self.assertEqual(df.chunks[1].shape, (3, 3))
        self.assertEqual(df.chunks[2].shape, (3, 3))
        self.assertEqual(df.chunks[3].shape, (1, 3))

        self.assertEqual(df.chunks[0].inputs[0].shape, (3,))
        self.assertEqual(df.chunks[1].inputs[0].shape, (3,))
        self.assertEqual(df.chunks[2].inputs[0].shape, (3,))
        self.assertEqual(df.chunks[3].inputs[0].shape, (1,))

        self.assertEqual(df.chunks[0].op.extra_params, {'begin_index': 0, 'end_index': 3})
        self.assertEqual(df.chunks[1].op.extra_params, {'begin_index': 3, 'end_index': 6})
        self.assertEqual(df.chunks[2].op.extra_params, {'begin_index': 6, 'end_index': 9})
        self.assertEqual(df.chunks[3].op.extra_params, {'begin_index': 9, 'end_index': 10})

        names = pd.Index(['x', 'y', 'z'])
        dtypes = pd.Series({'x': np.dtype('int'), 'y': np.dtype('double'), 'z': np.dtype('<U16')})
        for chunk in df.chunks:
            pd.testing.assert_index_equal(chunk.columns.to_pandas(), names)
            pd.testing.assert_series_equal(chunk.dtypes, dtypes)

        pd.testing.assert_index_equal(df.chunks[0].index_value.to_pandas(), pd.RangeIndex(0, 3))
        pd.testing.assert_index_equal(df.chunks[1].index_value.to_pandas(), pd.RangeIndex(3, 6))
        pd.testing.assert_index_equal(df.chunks[2].index_value.to_pandas(), pd.RangeIndex(6, 9))
        pd.testing.assert_index_equal(df.chunks[3].index_value.to_pandas(), pd.RangeIndex(9, 10))
