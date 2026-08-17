#!/usr/bin/env python3
import unittest

from render_report import render_report, build_stock_level_check, build_batch_serial_trace, RenderError


class TestRenderReport(unittest.TestCase):
    def test_requires_reconciliation_checks(self):
        with self.assertRaises(RenderError):
            render_report("Title", "Scope", [], [])

    def test_not_applicable_requires_notes(self):
        with self.assertRaises(RenderError):
            render_report("Title", "Scope", [], "not_applicable", notes="")

    def test_not_applicable_with_notes_ok(self):
        out = render_report("Title", "Scope", [], "not_applicable", notes="no total to tie out")
        self.assertIn("not_applicable", out)

    def test_failed_check_surfaced_as_anomaly(self):
        checks = [{"check": "sum vs total", "expected": 20, "actual": 14, "ties_out": False}]
        out = render_report("Stock level", "Item: SKU-1", [], checks)
        self.assertIn("ANOMALY", out)

    def test_passed_check_no_anomaly(self):
        checks = [{"check": "sum vs total", "expected": 20, "actual": 20, "ties_out": True}]
        out = render_report("Stock level", "Item: SKU-1", [], checks)
        self.assertNotIn("ANOMALY", out)


class TestBuildStockLevelCheck(unittest.TestCase):
    def test_ties_out_when_sum_matches(self):
        rows = [{"warehouse": "A", "actual_qty": 6}, {"warehouse": "B", "actual_qty": 4}]
        check = build_stock_level_check(rows, expected_total=10)
        self.assertTrue(check["ties_out"])

    def test_does_not_tie_out_when_sum_mismatches(self):
        rows = [{"warehouse": "A", "actual_qty": 6}, {"warehouse": "B", "actual_qty": 4}]
        check = build_stock_level_check(rows, expected_total=14)
        self.assertFalse(check["ties_out"])
        self.assertEqual(check["actual"], 10)


class TestBuildBatchSerialTrace(unittest.TestCase):
    def test_consistent_running_balance(self):
        entries = [
            {"posting_date": "2026-08-01", "actual_qty": 10, "qty_after_transaction": 10,
             "voucher_type": "Stock Entry", "voucher_no": "MAT-STE-1"},
            {"posting_date": "2026-08-05", "actual_qty": -4, "qty_after_transaction": 6,
             "voucher_type": "Stock Entry", "voucher_no": "MAT-STE-2"},
        ]
        result = build_batch_serial_trace(entries)
        self.assertTrue(result["running_balance_consistent"])
        self.assertEqual(result["final_qty"], 6)
        self.assertEqual(len(result["events"]), 2)

    def test_flags_inconsistent_running_balance(self):
        entries = [
            {"posting_date": "2026-08-01", "actual_qty": 10, "qty_after_transaction": 10,
             "voucher_type": "Stock Entry", "voucher_no": "MAT-STE-1"},
            {"posting_date": "2026-08-05", "actual_qty": -4, "qty_after_transaction": 99,
             "voucher_type": "Stock Entry", "voucher_no": "MAT-STE-2"},
        ]
        result = build_batch_serial_trace(entries)
        self.assertFalse(result["running_balance_consistent"])

    def test_empty_entries(self):
        result = build_batch_serial_trace([])
        self.assertEqual(result["events"], [])
        self.assertEqual(result["final_qty"], 0)
        self.assertTrue(result["running_balance_consistent"])


if __name__ == "__main__":
    unittest.main()
