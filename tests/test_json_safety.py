import json
import unittest
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
from backend.utils.json_safety import sanitize_for_json, validate_json_safety

class TestJsonSafety(unittest.TestCase):
    def test_basic_types(self):
        self.assertEqual(sanitize_for_json(None), None)
        self.assertEqual(sanitize_for_json("test"), "test")
        self.assertEqual(sanitize_for_json(True), True)
        self.assertEqual(sanitize_for_json(123), 123)
        self.assertEqual(sanitize_for_json(1.23), 1.23)

    def test_numpy_types(self):
        self.assertEqual(sanitize_for_json(np.int64(1)), 1)
        self.assertEqual(sanitize_for_json(np.float64(1.2)), 1.2)
        self.assertEqual(sanitize_for_json(np.bool_(True)), True)
        self.assertEqual(sanitize_for_json(np.array([1, 2, 3])), [1, 2, 3])
        self.assertEqual(sanitize_for_json(np.array(["a", "b"])), ["a", "b"])
        self.assertEqual(sanitize_for_json(np.nan), None)
        self.assertEqual(sanitize_for_json(np.inf), None)
        self.assertEqual(sanitize_for_json(-np.inf), None)

    def test_pandas_types(self):
        self.assertEqual(sanitize_for_json(pd.NA), None)
        self.assertEqual(sanitize_for_json(pd.NaT), None)
        self.assertEqual(sanitize_for_json(pd.Timestamp("2023-01-01")), "2023-01-01T00:00:00")
        
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        self.assertEqual(sanitize_for_json(df), [{"a": 1, "b": 3}, {"a": 2, "b": 4}])
        
        s = pd.Series([1, 2, 3])
        self.assertEqual(sanitize_for_json(s), [1, 2, 3])

    def test_collections(self):
        self.assertEqual(sanitize_for_json({1, 2}), [1, 2])
        self.assertEqual(sanitize_for_json((1, 2)), [1, 2])
        self.assertEqual(sanitize_for_json(Counter({"a": 1})), {"a": 1})
        
        nested = {
            "a": np.array([1, 2]),
            "b": {"c": pd.Timestamp("2023-01-01")},
            "d": [np.int64(10), Decimal("1.5")]
        }
        expected = {
            "a": [1, 2],
            "b": {"c": "2023-01-01T00:00:00"},
            "d": [10, 1.5]
        }
        self.assertEqual(sanitize_for_json(nested), expected)

    def test_others(self):
        self.assertEqual(sanitize_for_json(Path("/tmp/test")), "/tmp/test")
        self.assertEqual(sanitize_for_json(Decimal("10.5")), 10.5)

    def test_serialization_validation(self):
        payload = {
            "ndarray": np.array([1, 2, 3]),
            "timestamp": pd.Timestamp("2023-05-09"),
            "nan": np.nan,
            "int64": np.int64(100)
        }
        sanitized = sanitize_for_json(payload)
        # Should not raise exception
        json_str = json.dumps(sanitized, allow_nan=False)
        self.assertIn('"ndarray": [1, 2, 3]', json_str)
        self.assertTrue(validate_json_safety(sanitized))

if __name__ == "__main__":
    unittest.main()
