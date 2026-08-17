#!/usr/bin/env python3
"""Unit tests for render_report.py. Run: python scripts/test_render_report.py"""

import unittest

from render_report import RenderError, render_report


class TestReconciliationGate(unittest.TestCase):
    def test_refuses_empty_checks(self):
        with self.assertRaises(RenderError):
            render_report("T", "P", "C", [], [])

    def test_refuses_non_numeric_row_value(self):
        checks = [{"check": "x", "expected": 1, "actual": 1, "ties_out": True}]
        sections = [{"title": "S", "rows": [{"label": "bad", "value": "oops"}]}]
        with self.assertRaises(RenderError):
            render_report("T", "P", "C", sections, checks)

    def test_accepts_well_formed_aging_report(self):
        checks = [{"check": "bucket sum vs party total", "expected": 5000, "actual": 5000, "ties_out": True}]
        sections = [{"title": "Aging", "rows": [{"label": "0-30", "value": 3000}, {"label": "31-60", "value": 2000}],
                      "total": {"label": "Total", "value": 5000}}]
        out = render_report("AP Aging", "Aug 2026", "Acme", sections, checks)
        self.assertIn("AP Aging", out)
        self.assertIn("yes", out)

    def test_not_applicable_opt_out(self):
        out = render_report("3-Way Match Discrepancies", "FY26", "Acme", [], "not_applicable",
                             notes="Discrepancy list, not a total to reconcile.")
        self.assertIn("No tie-out check applies", out)

    def test_failure_surfaces_as_anomaly(self):
        checks = [{"check": "bucket sum vs total", "expected": 5000, "actual": 4800, "ties_out": False}]
        out = render_report("AP Aging", "Aug 2026", "Acme", [], checks)
        self.assertIn("ANOMALY", out)
        self.assertIn("**NO**", out)

    def test_bank_reconciliation_detail_column_appears_when_present(self):
        # Bank reconciliation's unmatched lines each need a stated
        # hypothesis (not yet recorded vs not yet cleared) - `detail`
        # is how that gets structurally represented rather than smuggled
        # into a label string.
        sections = [{"title": "Unmatched lines", "rows": [
            {"label": "Cheque #4521", "value": 5000, "detail": "outstanding - not yet cleared by bank"},
        ]}]
        out = render_report("Bank Reconciliation", "Aug 2026", "Acme", sections, "not_applicable",
                             notes="discrepancy list only")
        self.assertIn("Detail", out)
        self.assertIn("not yet cleared by bank", out)

    def test_no_detail_column_when_absent(self):
        sections = [{"title": "Aging", "rows": [{"label": "0-30", "value": 3000}]}]
        checks = [{"check": "x", "expected": 3000, "actual": 3000, "ties_out": True}]
        out = render_report("AP Aging", "Aug 2026", "Acme", sections, checks)
        self.assertNotIn("Detail", out)


if __name__ == "__main__":
    unittest.main()
