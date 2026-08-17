#!/usr/bin/env python3
import unittest

from render_report import RenderError, build_grn_match, build_quotation_coverage, render_report


class TestRenderReport(unittest.TestCase):
    def test_requires_reconciliation_checks_or_not_applicable(self):
        with self.assertRaises(RenderError):
            render_report(title="t", scope="s", sections=[], reconciliation_checks=[])

    def test_not_applicable_requires_reason_in_notes(self):
        with self.assertRaises(RenderError):
            render_report(title="t", scope="s", sections=[], reconciliation_checks="not_applicable", notes="")

    def test_not_applicable_with_reason_ok(self):
        out = render_report(
            title="PO status", scope="PUR-ORD-2026-00007",
            sections=[{"title": "Status", "rows": [{"label": "status", "value": "To Bill"}]}],
            reconciliation_checks="not_applicable",
            notes="Status lookup has no total to tie out.",
        )
        self.assertIn("not_applicable", out)
        self.assertIn("To Bill", out)

    def test_ties_out_reported_clean(self):
        out = render_report(
            title="GRN match", scope="PO PUR-ORD-1 vs GRN MAT-PRE-1",
            sections=[{"title": "Quantities", "rows": [{"label": "ordered", "value": 100}, {"label": "received", "value": 100}]}],
            reconciliation_checks=[{"check": "received == ordered", "expected": 100, "actual": 100, "ties_out": True}],
        )
        self.assertNotIn("ANOMALY", out)
        self.assertIn("| received == ordered |", out)

    def test_anomaly_surfaced_prominently(self):
        out = render_report(
            title="GRN match", scope="PO PUR-ORD-1 vs GRN MAT-PRE-1",
            sections=[{"title": "Quantities", "rows": [{"label": "ordered", "value": 100}, {"label": "received", "value": 80}]}],
            reconciliation_checks=[{"check": "received == ordered", "expected": 100, "actual": 80, "ties_out": False}],
        )
        self.assertIn("ANOMALY", out)
        self.assertIn("**NO**", out)

    def test_malformed_check_rejected(self):
        with self.assertRaises(RenderError):
            render_report(
                title="t", scope="s",
                sections=[],
                reconciliation_checks=[{"check": "x", "expected": 1, "actual": 1}],  # missing ties_out
            )

    def test_non_numeric_check_value_rejected(self):
        with self.assertRaises(RenderError):
            render_report(
                title="t", scope="s",
                sections=[],
                reconciliation_checks=[{"check": "x", "expected": "a", "actual": 1, "ties_out": True}],
            )

    def test_string_row_value_allowed(self):
        out = render_report(
            title="Scorecard", scope="Supplier: Mauli Tea",
            sections=[{"title": "Standing", "rows": [{"label": "standing", "value": "Preferred"}]}],
            reconciliation_checks="not_applicable",
            notes="Scorecard standing is categorical, not a total to tie out.",
        )
        self.assertIn("Preferred", out)


class TestQuotationCoverage(unittest.TestCase):
    def test_full_coverage_flagged_complete(self):
        coverage = build_quotation_coverage(
            all_item_codes=["A", "B", "C"],
            supplier_quoted_items={"Mauli Tea": ["A", "B", "C"]},
        )
        self.assertTrue(coverage["Mauli Tea"]["complete"])
        self.assertEqual(coverage["Mauli Tea"]["missing"], [])

    def test_partial_coverage_names_missing_items(self):
        coverage = build_quotation_coverage(
            all_item_codes=["A", "B", "C"],
            supplier_quoted_items={"Mauli Tea": ["A", "B"]},
        )
        self.assertFalse(coverage["Mauli Tea"]["complete"])
        self.assertEqual(coverage["Mauli Tea"]["missing"], ["C"])
        self.assertEqual(coverage["Mauli Tea"]["quoted"], 2)
        self.assertEqual(coverage["Mauli Tea"]["of"], 3)

    def test_multiple_suppliers_compared_independently(self):
        coverage = build_quotation_coverage(
            all_item_codes=["A", "B"],
            supplier_quoted_items={"S1": ["A", "B"], "S2": ["A"]},
        )
        self.assertTrue(coverage["S1"]["complete"])
        self.assertFalse(coverage["S2"]["complete"])


class TestGrnMatch(unittest.TestCase):
    def test_clean_full_receipt(self):
        result = build_grn_match(
            po_items=[{"item_code": "Raw Item-1", "po_detail": "row-1", "qty": 5}],
            receipt_items=[{"purchase_order_item": "row-1", "received_qty": 5, "rejected_qty": 0}],
        )
        self.assertTrue(result["clean"])
        self.assertEqual(result["discrepancies"], [])

    def test_not_received_at_all(self):
        result = build_grn_match(
            po_items=[{"item_code": "Raw Item-1", "po_detail": "row-1", "qty": 5}],
            receipt_items=[],
        )
        self.assertFalse(result["clean"])
        self.assertEqual(result["discrepancies"][0]["issue"], "not_received")

    def test_partial_receipt_flagged(self):
        result = build_grn_match(
            po_items=[{"item_code": "Raw Item-1", "po_detail": "row-1", "qty": 10}],
            receipt_items=[{"purchase_order_item": "row-1", "received_qty": 6, "rejected_qty": 0}],
        )
        self.assertEqual(result["discrepancies"][0]["issue"], "partial_receipt")

    def test_over_receipt_flagged(self):
        result = build_grn_match(
            po_items=[{"item_code": "Raw Item-1", "po_detail": "row-1", "qty": 5}],
            receipt_items=[{"purchase_order_item": "row-1", "received_qty": 7, "rejected_qty": 0}],
        )
        self.assertEqual(result["discrepancies"][0]["issue"], "over_receipt")

    def test_full_receipt_with_rejection_flagged_separately_from_quantity(self):
        # Fully received (qty ties out) but partly rejected — a distinct
        # supplier-quality signal, not the same fact as under-receipt.
        result = build_grn_match(
            po_items=[{"item_code": "Raw Item-1", "po_detail": "row-1", "qty": 5}],
            receipt_items=[{"purchase_order_item": "row-1", "received_qty": 5, "rejected_qty": 2}],
        )
        issues = [d["issue"] for d in result["discrepancies"]]
        self.assertEqual(issues, ["rejected_quantity"])

    def test_walks_every_line_not_just_first_mismatch(self):
        result = build_grn_match(
            po_items=[
                {"item_code": "Item-A", "po_detail": "row-1", "qty": 5},
                {"item_code": "Item-B", "po_detail": "row-2", "qty": 3},
            ],
            receipt_items=[
                {"purchase_order_item": "row-1", "received_qty": 3, "rejected_qty": 0},
                {"purchase_order_item": "row-2", "received_qty": 3, "rejected_qty": 1},
            ],
        )
        item_codes = {d["item_code"] for d in result["discrepancies"]}
        self.assertEqual(item_codes, {"Item-A", "Item-B"})

    def test_multiple_receipts_against_one_po_line_summed(self):
        result = build_grn_match(
            po_items=[{"item_code": "Raw Item-1", "po_detail": "row-1", "qty": 10}],
            receipt_items=[
                {"purchase_order_item": "row-1", "received_qty": 4, "rejected_qty": 0},
                {"purchase_order_item": "row-1", "received_qty": 6, "rejected_qty": 0},
            ],
        )
        self.assertTrue(result["clean"])


if __name__ == "__main__":
    unittest.main()
