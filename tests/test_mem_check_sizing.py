"""Bounds tests for mem_check's auto buffer sizing (beam-campaign policy)."""

import importlib.util
import sys
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "mem_check",
    Path(__file__).resolve().parents[1] / "jetson" / "memory" / "mem_check.py",
)
mem_check = importlib.util.module_from_spec(SPEC)
sys.modules.setdefault("mem_check", mem_check)
SPEC.loader.exec_module(mem_check)

AUTO = {"buffer_mb": "auto"}


class ResolveBufferMbTests(unittest.TestCase):
    def test_ceiling_binds_on_a_roomy_system(self):
        # 0.60 * 6000 = 3600 and 6000 - 2304 = 3696 both exceed the ceiling.
        self.assertEqual(mem_check.resolve_buffer_mb(dict(AUTO), 6000), 3481)

    def test_fraction_binds_on_a_mid_system(self):
        # 0.60 * 5000 = 3000 < ceiling 3481 and < 5000 - 2304 = 2696? No:
        # 2696 < 3000, so the reserve binds here -- use a roomier reserve case.
        cfg = dict(AUTO, auto_reserve_mb=1000)
        self.assertEqual(mem_check.resolve_buffer_mb(cfg, 5000), 3000)

    def test_reserve_binds_on_a_tight_system(self):
        # 4000 - 2304 = 1696 is the smallest bound (fraction would take 2400).
        self.assertEqual(mem_check.resolve_buffer_mb(dict(AUTO), 4000), 1696)

    def test_never_below_floor(self):
        # Reserve exceeds what's available -> clamped to the 64 MB minimum.
        self.assertEqual(mem_check.resolve_buffer_mb(dict(AUTO), 2000), 64)

    def test_fixed_integer_passes_through(self):
        self.assertEqual(mem_check.resolve_buffer_mb({"buffer_mb": 2805}, 6000), 2805)

    def test_config_overrides_apply(self):
        cfg = dict(AUTO, auto_max_mb=3000, auto_fraction=0.5, auto_reserve_mb=500)
        self.assertEqual(mem_check.resolve_buffer_mb(cfg, 5000), 2500)


if __name__ == "__main__":
    unittest.main()
