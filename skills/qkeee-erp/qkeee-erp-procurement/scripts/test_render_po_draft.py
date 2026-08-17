#!/usr/bin/env python3
import unittest

from render_po_draft import render_po_draft


class TestPODraft(unittest.TestCase):
    def _header(self):
        return {"supplier": "Mauli Tea", "company": "Qkeee LLP", "transaction_date": "2026-08-10", "currency": "INR"}

    def test_complete_po_defaults_draft_only(self):
        result = render_po_draft(
            header=self._header(),
            items=[{"item_code": "Raw Item-1", "qty": 5, "rate": 10, "warehouse": "Stores - QL"}],
            stock_items={"Raw Item-1"},
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["recommended_action"], "create-as-draft-only")
        self.assertEqual(result["total"], 50)

    def test_authority_confirmed_allows_submit_path(self):
        result = render_po_draft(
            header=self._header(),
            items=[{"item_code": "Raw Item-1", "qty": 5, "rate": 10, "warehouse": "Stores - QL"}],
            stock_items={"Raw Item-1"},
            submission_authority_confirmed=True,
        )
        self.assertEqual(result["recommended_action"], "create-then-submit-on-confirm")

    def test_missing_warehouse_on_stock_item_flagged(self):
        result = render_po_draft(
            header=self._header(),
            items=[{"item_code": "Raw Item-1", "qty": 5, "rate": 10}],
            stock_items={"Raw Item-1"},
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("warehouse" in p for p in result["problems"]))

    def test_service_item_does_not_need_warehouse(self):
        result = render_po_draft(
            header=self._header(),
            items=[{"item_code": "Consulting", "qty": 1, "rate": 500}],
            stock_items=set(),
        )
        self.assertEqual(result["status"], "ready")

    def test_defaults_to_stock_when_not_told_otherwise(self):
        result = render_po_draft(
            header=self._header(),
            items=[{"item_code": "Raw Item-1", "qty": 5, "rate": 10}],
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("warehouse" in p for p in result["problems"]))

    def test_missing_header_field_flagged(self):
        result = render_po_draft(header={"supplier": "Mauli Tea"}, items=[])
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("header.company" in p for p in result["problems"]))
        self.assertTrue(any("items:" in p for p in result["problems"]))

    def test_incomplete_never_recommends_submit(self):
        result = render_po_draft(header={}, items=[])
        self.assertEqual(result["recommended_action"], "fix-before-staging")


if __name__ == "__main__":
    unittest.main()
