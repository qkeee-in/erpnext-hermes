#!/usr/bin/env python3
import unittest

from render_report import RenderError, render_report


class TestRenderReport(unittest.TestCase):
    def test_requires_reconciliation_checks_or_not_applicable(self):
        with self.assertRaises(RenderError):
            render_report(title="t", scope="s", sections=[], reconciliation_checks=[])

    def test_not_applicable_requires_reason(self):
        with self.assertRaises(RenderError):
            render_report(title="t", scope="s", sections=[], reconciliation_checks="not_applicable", notes="")

    def test_birthday_list_not_applicable_with_reason(self):
        out = render_report(
            title="Upcoming birthdays", scope="Next 30 days",
            sections=[{"title": "Employees", "rows": [{"label": "Jane Doe", "value": "2026-08-15"}]}],
            reconciliation_checks="not_applicable",
            notes="A birthday list has no total to tie out.",
        )
        self.assertIn("Jane Doe", out)

    def test_headcount_reconciliation_ties_out(self):
        out = render_report(
            title="Headcount", scope="As of 2026-08-10",
            sections=[{"title": "By department", "rows": [{"label": "Engineering", "value": 10}, {"label": "Sales", "value": 5}]}],
            reconciliation_checks=[{"check": "sum(departments) == total headcount", "expected": 15, "actual": 15, "ties_out": True}],
        )
        self.assertNotIn("ANOMALY", out)

    def test_boolean_row_value_rejected_before_reconciliation_trusted(self):
        # Regression: adversarial review — row validation used
        # to run after reconciliation_checks validation; now row shape is
        # checked first.
        with self.assertRaises(RenderError):
            render_report(
                title="t", scope="s",
                sections=[{"title": "s", "rows": [{"label": "x", "value": True}]}],
                reconciliation_checks=[{"check": "c", "expected": 1, "actual": 1, "ties_out": True}],
            )

    def test_headcount_anomaly_surfaced(self):
        out = render_report(
            title="Headcount", scope="As of 2026-08-10",
            sections=[{"title": "By department", "rows": [{"label": "Engineering", "value": 10}]}],
            reconciliation_checks=[{"check": "sum(departments) == total headcount", "expected": 15, "actual": 10, "ties_out": False}],
        )
        self.assertIn("ANOMALY", out)
        self.assertIn("**NO**", out)


if __name__ == "__main__":
    unittest.main()
