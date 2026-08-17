#!/usr/bin/env python3
import unittest

from render_depreciation_run import render_depreciation_run, RenderError

ROWS = [
    {"schedule_date": "2026-02-28", "depreciation_amount": 100, "accumulated_depreciation_amount": 100},
    {"schedule_date": "2026-03-31", "depreciation_amount": 100, "accumulated_depreciation_amount": 200},
]


class TestRenderDepreciationRun(unittest.TestCase):
    def test_empty_pending_rows_raises(self):
        with self.assertRaises(RenderError):
            render_depreciation_run("ACC-ASS-1", "ACC-ADS-1", "2026-04-01", 1200, [])

    def test_non_numeric_opening_raises(self):
        with self.assertRaises(RenderError):
            render_depreciation_run("ACC-ASS-1", "ACC-ADS-1", "2026-04-01", "n/a", ROWS)

    def test_row_missing_key_raises(self):
        with self.assertRaises(RenderError):
            render_depreciation_run(
                "ACC-ASS-1", "ACC-ADS-1", "2026-04-01", 1200,
                [{"schedule_date": "2026-02-28", "depreciation_amount": 100}],
            )

    def test_states_multi_period_batching(self):
        out = render_depreciation_run("ACC-ASS-1", "ACC-ADS-1", "2026-04-01", 1200, ROWS)
        self.assertIn("2 period(s) are due and will ALL post in one call", out)
        self.assertIn("NOT POSTED YET", out)
        self.assertIn("Double confirm", out)

    def test_computes_resulting_book_value(self):
        out = render_depreciation_run("ACC-ASS-1", "ACC-ADS-1", "2026-04-01", 1200, ROWS)
        self.assertIn("Total depreciation this run:** 200.00", out)
        self.assertIn("Resulting book value:** 1,000.00", out)

    def test_flags_stale_top_level_field(self):
        out = render_depreciation_run("ACC-ASS-1", "ACC-ADS-1", "2026-04-01", 1200, ROWS)
        self.assertIn("value_after_depreciation` will NOT reflect", out)

    def test_wrong_book_value_source_raises(self):
        with self.assertRaises(RenderError):
            render_depreciation_run("ACC-ASS-1", "ACC-ADS-1", "2026-04-01", 1200, ROWS,
                                     book_value_source="top_level")

    def test_emits_confirmation_token(self):
        out = render_depreciation_run("ACC-ASS-1", "ACC-ADS-1", "2026-04-01", 1200, ROWS)
        self.assertIn("Confirmation token:", out)


if __name__ == "__main__":
    unittest.main()
