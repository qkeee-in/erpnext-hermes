#!/usr/bin/env python3
"""Unit tests for render_report.py. Run: python scripts/test_render_report.py"""

import unittest

from render_report import RenderError, compute_variance, render_report


class TestReconciliationGate(unittest.TestCase):
    def test_refuses_empty_checks(self):
        with self.assertRaises(RenderError):
            render_report("T", "P", "C", [], [])

    def test_refuses_missing_key(self):
        with self.assertRaises(RenderError):
            render_report("T", "P", "C", [], [{"check": "x", "expected": 1, "actual": 1}])

    def test_refuses_non_numeric_check_figure(self):
        with self.assertRaises(RenderError):
            render_report("T", "P", "C", [],
                           [{"check": "x", "expected": "1", "actual": 1, "ties_out": True}])

    def test_refuses_non_numeric_row_value(self):
        checks = [{"check": "x", "expected": 1, "actual": 1, "ties_out": True}]
        sections = [{"title": "S", "rows": [{"label": "bad", "value": "not-a-number"}]}]
        with self.assertRaises(RenderError):
            render_report("T", "P", "C", sections, checks)

    def test_accepts_well_formed_report(self):
        checks = [{"check": "debit vs credit", "expected": 100, "actual": 100, "ties_out": True}]
        sections = [{"title": "Accounts", "rows": [{"label": "Cash", "value": 100}]}]
        out = render_report("Trial Balance", "Apr 2026", "Acme", sections, checks)
        self.assertIn("Trial Balance", out)
        self.assertIn("yes", out)

    def test_not_applicable_opt_out_renders(self):
        out = render_report("Supplier List", "FY26", "Acme", [], "not_applicable",
                             notes="No total to reconcile — this is a membership list, not a figure.")
        self.assertIn("No tie-out check applies", out)

    def test_failure_surfaces_as_anomaly_not_hidden(self):
        checks = [{"check": "assets vs liab+equity", "expected": 100, "actual": 90, "ties_out": False}]
        out = render_report("Balance Sheet", "FY26", "Acme", [], checks)
        self.assertIn("ANOMALY", out)
        self.assertIn("**NO**", out)

    def test_non_numeric_mismatch_diff_not_dropped(self):
        checks = [{"check": "party name match", "expected": 1, "actual": 2, "ties_out": False}]
        out = render_report("X", "P", "C", [], checks)
        self.assertIn("diff", out)
        self.assertNotIn("diff n/a)", out)


class TestComputeVariance(unittest.TestCase):
    def test_normal_variance(self):
        result = compute_variance(actual=120, comparison=100)
        self.assertEqual(result["variance"], 20)
        self.assertAlmostEqual(result["variance_pct"], 0.2)

    def test_zero_comparison_guarded(self):
        result = compute_variance(actual=50, comparison=0)
        self.assertEqual(result["variance"], 50)
        self.assertIsNone(result["variance_pct"])


if __name__ == "__main__":
    unittest.main()
