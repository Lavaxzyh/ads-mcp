"""S-parameter regressions use asymmetric synthetic data, without ADS."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from ads_engine import AdsEngine


def frame(n=2):
    return pd.DataFrame({"S[1,1]": [0.11+0.01j]*n, "S[2,1]": [0.21+0.02j]*n,
                         "S[1,2]": [0.12+0.03j]*n, "S[2,2]": [0.22+0.04j]*n},
                        index=pd.Index(np.linspace(5e7, 4e9, n), name="freq"))


class ResultsTests(unittest.TestCase):
    def test_touchstone_two_port_order_is_column_major(self):
        with tempfile.TemporaryDirectory(prefix="ads-results-unit-") as tmp:
            engine = AdsEngine()
            target = Path(tmp) / "asymmetric.s2p"
            with patch.object(engine, "_read_dataframe", return_value=(["data"], frame())):
                engine.export_touchstone("input.ds", str(target))
            row = [float(x) for x in target.read_text().splitlines()[3].split()]
            self.assertEqual(row[1:], [0.11, 0.01, 0.21, 0.02, 0.12, 0.03, 0.22, 0.04])

    def test_preview_is_bounded_and_keeps_last_frequency(self):
        df = frame(396)
        rows = AdsEngine._s_preview(df, list(df.columns), max_points=101)
        self.assertEqual(len(rows), 101)
        self.assertEqual(rows[-1]["freq_hz"], 4e9)

    def test_three_port_dataset_cannot_be_mislabeled_s2p(self):
        df = frame()
        df["S[3,1]"] = 0
        engine = AdsEngine()
        with patch.object(engine, "_read_dataframe", return_value=(["data"], df)):
            with self.assertRaisesRegex(ValueError, "two-port"):
                engine.export_touchstone("unused.ds", "unused.s2p")

    def test_momentum_grid_rejects_same_count_with_different_frequencies(self):
        engine = AdsEngine()
        expected = {"start_hz": 5e7, "stop_hz": 4e9, "points": 396}
        df = frame(396)
        df.index = df.index.to_numpy() + 1000
        with patch.object(engine, "_read_dataframe", return_value=(["data"], df)):
            with self.assertRaisesRegex(ValueError, "frequency grid"):
                engine.validate_momentum_dataset("x.ds", expected)

    def test_first_auxiliary_varblock_is_skipped_and_frequencies_are_sorted(self):
        df = frame().iloc[::-1]
        blocks = {"aux": types.SimpleNamespace(to_dataframe=lambda: pd.DataFrame({"x": [1]})),
                  "data": types.SimpleNamespace(to_dataframe=lambda: df)}
        dataset = types.ModuleType("keysight.ads.dataset")
        dataset.open = lambda *args, **kwargs: blocks
        keysight = types.ModuleType("keysight")
        ads = types.ModuleType("keysight.ads")
        keysight.ads = ads
        ads.dataset = dataset
        with patch.dict(sys.modules, {"keysight": keysight, "keysight.ads": ads, "keysight.ads.dataset": dataset}):
            keys, actual = AdsEngine._read_dataframe("unused.ds")
        self.assertEqual(keys, ["aux", "data"])
        self.assertTrue(actual.index.is_monotonic_increasing)


if __name__ == "__main__":
    unittest.main()
