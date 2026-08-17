#!/usr/bin/env python3
import unittest

from render_report import build_so_status_report, build_dn_tracking_report, build_pipeline


class TestSoStatusReport(unittest.TestCase):
    def test_maps_fields(self):
        result = build_so_status_report([{
            "name": "SAL-ORD-2026-00005", "status": "To Bill", "delivery_status": "Fully Delivered",
            "per_delivered": 100.0, "billing_status": "Not Billed", "per_billed": 0.0, "grand_total": 45.0,
        }])
        self.assertEqual(result["rows"][0]["sales_order"], "SAL-ORD-2026-00005")
        self.assertEqual(result["rows"][0]["per_delivered"], 100.0)
        self.assertEqual(result["reconciliation_checks"], "not_applicable")
        self.assertIn("nothing to tie out", result["notes"])


class TestDnTrackingReport(unittest.TestCase):
    def test_maps_fields(self):
        result = build_dn_tracking_report([{
            "name": "MAT-DN-2026-00002", "status": "To Bill",
            "against_sales_order": "SAL-ORD-2026-00005", "grand_total": 45.0,
        }])
        self.assertEqual(result["rows"][0]["delivery_note"], "MAT-DN-2026-00002")
        self.assertEqual(result["rows"][0]["against_sales_order"], "SAL-ORD-2026-00005")

    def test_so_detail_not_checked_without_dn_items(self):
        result = build_dn_tracking_report([{"name": "MAT-DN-2026-00002", "status": "To Bill"}])
        self.assertIn("NOT checked", result["notes"])

    def test_so_detail_missing_flagged_when_dn_items_given(self):
        result = build_dn_tracking_report(
            dn_rows=[{"name": "MAT-DN-2026-00002", "status": "To Bill"}],
            dn_items=[{"parent": "MAT-DN-2026-00002", "against_sales_order": "SAL-ORD-2026-00005", "so_detail": None}],
        )
        self.assertIn("WARNING", result["notes"])
        self.assertIn("MAT-DN-2026-00002", result["notes"])
        # The warning must name the affected Sales Order, not just the DN —
        # the report's stated purpose is diagnosing SO-side fulfilment
        # mismatches, so the SO name has to be in the message itself.
        self.assertIn("SAL-ORD-2026-00005", result["notes"])

    def test_so_detail_present_no_warning(self):
        result = build_dn_tracking_report(
            dn_rows=[{"name": "MAT-DN-2026-00002", "status": "To Bill"}],
            dn_items=[{"parent": "MAT-DN-2026-00002", "against_sales_order": "SAL-ORD-2026-00005", "so_detail": "568fi4rtrs"}],
        )
        self.assertNotIn("WARNING", result["notes"])


class TestPipeline(unittest.TestCase):
    def test_reconciles_when_counts_match_total(self):
        result = build_pipeline({"Draft": 2, "Open": 3}, total_quotations_queried=5)
        self.assertEqual(result["reconciliation_checks"], "passed")
        self.assertEqual(result["issues"], [])

    def test_flags_mismatch(self):
        result = build_pipeline({"Draft": 2, "Open": 3}, total_quotations_queried=6)
        self.assertEqual(result["reconciliation_checks"], "failed")
        self.assertTrue(any("don't reconcile" in i for i in result["issues"]))

    def test_no_total_given_skips_reconciliation_check_but_still_passes(self):
        result = build_pipeline({"Draft": 2, "Open": 3})
        self.assertEqual(result["reconciliation_checks"], "passed")

    def test_so_summary_included_when_analysis_rows_given(self):
        result = build_pipeline(
            {"Ordered": 1},
            so_analysis_rows=[
                {"pending_amount": 120.0, "delay_days": 22},
                {"pending_amount": 30.0, "delay_days": 0},
            ],
        )
        self.assertEqual(result["sales_order_summary"]["open_so_lines"], 2)
        self.assertEqual(result["sales_order_summary"]["total_pending_amount"], 150.0)
        self.assertEqual(result["sales_order_summary"]["overdue_lines"], 1)

    def test_no_so_rows_leaves_summary_none(self):
        result = build_pipeline({"Draft": 1})
        self.assertIsNone(result["sales_order_summary"])


if __name__ == "__main__":
    unittest.main()
