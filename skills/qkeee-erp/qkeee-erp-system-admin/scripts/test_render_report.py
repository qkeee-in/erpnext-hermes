#!/usr/bin/env python3
import unittest

from render_report import render_report, RenderError

SECTIONS = [{
    "title": "Roles with write access",
    "rows": [
        {"label": "System Manager", "value": 1},
        {"label": "Auditor", "value": 0},
    ],
}]


class TestRenderReport(unittest.TestCase):
    def test_no_checks_declared_raises(self):
        with self.assertRaises(RenderError):
            render_report("Permission matrix", "2026", "Contact", SECTIONS, [])

    def test_not_applicable_requires_reason_in_notes_by_convention(self):
        out = render_report("Permission matrix review", "2026", "Contact", SECTIONS,
                             "not_applicable", notes="Permission matrix has nothing numeric to tie out.")
        self.assertIn("not_applicable", out)
        self.assertIn("nothing numeric", out)

    def test_failed_check_surfaced_as_anomaly(self):
        checks = [{"check": "active users vs enabled users", "expected": 5, "actual": 4, "ties_out": False}]
        out = render_report("System health check", "2026", "all", SECTIONS, checks)
        self.assertIn("ANOMALY", out)
        self.assertIn("**NO**", out)

    def test_passing_check_no_anomaly(self):
        checks = [{"check": "active users vs enabled users", "expected": 5, "actual": 5, "ties_out": True}]
        out = render_report("System health check", "2026", "all", SECTIONS, checks)
        self.assertNotIn("ANOMALY", out)

    def test_non_numeric_row_value_raises(self):
        bad_sections = [{"title": "x", "rows": [{"label": "a", "value": "n/a"}]}]
        checks = [{"check": "c", "expected": 1, "actual": 1, "ties_out": True}]
        with self.assertRaises(RenderError):
            render_report("Title", "2026", "all", bad_sections, checks)

    def test_null_row_value_allowed_for_status_only_rows(self):
        sections = [{"title": "Scheduled jobs", "rows": [{"label": "backup job", "value": None, "detail": "not stopped"}]}]
        out = render_report("System health check", "2026", "all", sections, "not_applicable", notes="status checklist")
        self.assertIn("not stopped", out)


if __name__ == "__main__":
    unittest.main()
