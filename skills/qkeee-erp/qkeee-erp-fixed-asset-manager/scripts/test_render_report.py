#!/usr/bin/env python3
import unittest

from render_report import render_report, build_schedule_reconciliation, RenderError

SECTIONS = [{
    "title": "Depreciation schedule",
    "rows": [
        {"label": "2026-02-28", "value": 100},
        {"label": "2026-03-31", "value": 100},
    ],
    "total": {"label": "Total", "value": 200},
}]


class TestBuildScheduleReconciliation(unittest.TestCase):
    def test_ties_out_when_matching(self):
        rows = [{"depreciation_amount": 100}, {"depreciation_amount": 100}]
        check = build_schedule_reconciliation(1200, 0, 1000, rows)
        self.assertEqual(check["expected"], 200)
        self.assertEqual(check["actual"], 200)
        self.assertTrue(check["ties_out"])

    def test_flags_when_not_matching(self):
        rows = [{"depreciation_amount": 100}]
        check = build_schedule_reconciliation(1200, 0, 1000, rows)
        self.assertFalse(check["ties_out"])

    def test_dedupes_superseded_rows_by_schedule_date(self):
        # A stale pre-regeneration row for 2026-02-28 (amount 90) is
        # superseded by a later row for the same date (amount 100) —
        # only the last one for each date should count.
        rows = [
            {"schedule_date": "2026-02-28", "depreciation_amount": 90},
            {"schedule_date": "2026-02-28", "depreciation_amount": 100},
            {"schedule_date": "2026-03-31", "depreciation_amount": 100},
        ]
        check = build_schedule_reconciliation(1200, 0, 1000, rows)
        self.assertEqual(check["actual"], 200)
        self.assertTrue(check["ties_out"])


class TestRenderReport(unittest.TestCase):
    def test_no_checks_declared_raises(self):
        with self.assertRaises(RenderError):
            render_report("Schedule review", "2026", "Qkeee LLP", SECTIONS, [])

    def test_not_applicable_requires_reason_in_notes_by_convention(self):
        out = render_report("Audit checklist", "2026", "Qkeee LLP", SECTIONS, "not_applicable",
                             notes="Checklist has no single figure to tie out.")
        self.assertIn("not_applicable", out)
        self.assertIn("no single figure", out)

    def test_failed_check_surfaced_as_anomaly(self):
        checks = [{"check": "sched vs base", "expected": 200, "actual": 150, "ties_out": False}]
        out = render_report("Schedule review", "2026", "Qkeee LLP", SECTIONS, checks)
        self.assertIn("ANOMALY", out)
        self.assertIn("**NO**", out)

    def test_passing_check_no_anomaly(self):
        checks = [{"check": "sched vs base", "expected": 200, "actual": 200, "ties_out": True}]
        out = render_report("Schedule review", "2026", "Qkeee LLP", SECTIONS, checks)
        self.assertNotIn("ANOMALY", out)

    def test_non_numeric_row_value_raises(self):
        bad_sections = [{"title": "x", "rows": [{"label": "a", "value": "n/a"}]}]
        checks = [{"check": "c", "expected": 1, "actual": 1, "ties_out": True}]
        with self.assertRaises(RenderError):
            render_report("Title", "2026", "Qkeee LLP", bad_sections, checks)

    def test_null_row_value_allowed_for_status_only_rows(self):
        sections = [{"title": "Audit items", "rows": [{"label": "Laptop #1 present", "value": None, "detail": "confirmed"}]}]
        out = render_report("Audit checklist", "2026", "Qkeee LLP", sections, "not_applicable", notes="checklist")
        self.assertIn("confirmed", out)


if __name__ == "__main__":
    unittest.main()
