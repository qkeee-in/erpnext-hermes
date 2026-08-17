#!/usr/bin/env python3
import unittest

from render_reconciliation_draft import render_reconciliation_draft, RenderError


class TestRenderReconciliationDraft(unittest.TestCase):
    def test_empty_items_raises(self):
        with self.assertRaises(RenderError):
            render_reconciliation_draft("Qkeee LLP", "2026-08-10", [])

    def test_item_missing_required_key_raises(self):
        with self.assertRaises(RenderError):
            render_reconciliation_draft("Qkeee LLP", "2026-08-10", [{"item_code": "Raw Item-1"}])

    def test_ready_for_non_batch_item_with_current_qty(self):
        items = [{"item_code": "Raw Item-1", "warehouse": "Stores - QL", "qty": 20,
                  "current_qty": 0, "valuation_rate": 2, "current_valuation_rate": 0}]
        out = render_reconciliation_draft("Qkeee LLP", "2026-08-10", items)
        self.assertIn("READY", out)

    def test_incomplete_when_current_qty_missing(self):
        items = [{"item_code": "Raw Item-1", "warehouse": "Stores - QL", "qty": 20}]
        out = render_reconciliation_draft("Qkeee LLP", "2026-08-10", items)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("no current_qty supplied", out)

    def test_incomplete_when_batch_tracked_item_missing_batch_no(self):
        items = [{"item_code": "SKU-1", "warehouse": "Stores - QL", "qty": 8, "current_qty": 6,
                  "resolved_via_get_items": True}]
        out = render_reconciliation_draft(
            "Qkeee LLP", "2026-08-10", items, batch_tracked_items={"SKU-1"}
        )
        self.assertIn("INCOMPLETE", out)
        self.assertIn("batch/serial-tracked but", out)

    def test_incomplete_when_batch_tracked_item_not_resolved_via_get_items(self):
        items = [{"item_code": "SKU-1", "warehouse": "Stores - QL", "qty": 8, "current_qty": 0,
                  "batch_no": "SKU1-00007"}]
        out = render_reconciliation_draft(
            "Qkeee LLP", "2026-08-10", items, batch_tracked_items={"SKU-1"}
        )
        self.assertIn("INCOMPLETE", out)
        self.assertIn("resolved_via_get_items is not True", out)

    def test_ready_when_batch_tracked_item_fully_resolved(self):
        items = [{"item_code": "SKU-1", "warehouse": "Stores - QL", "qty": 8, "current_qty": 6,
                  "batch_no": "SKU1-00007", "resolved_via_get_items": True}]
        out = render_reconciliation_draft(
            "Qkeee LLP", "2026-08-10", items, batch_tracked_items={"SKU-1"}
        )
        self.assertIn("READY", out)

    def test_qty_and_value_delta_shown(self):
        items = [{"item_code": "Raw Item-1", "warehouse": "Stores - QL", "qty": 20,
                  "current_qty": 5, "valuation_rate": 2, "current_valuation_rate": 1}]
        out = render_reconciliation_draft("Qkeee LLP", "2026-08-10", items)
        self.assertIn("Total qty delta: 15.00", out)
        # (20*2) - (5*1) = 35
        self.assertIn("Total value delta: 35.00", out)

    def test_negative_qty_is_incomplete(self):
        items = [{"item_code": "Raw Item-1", "warehouse": "Stores - QL", "qty": -1, "current_qty": 5}]
        out = render_reconciliation_draft("Qkeee LLP", "2026-08-10", items)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("cannot be negative", out)

    def test_valuation_rate_without_current_rate_is_incomplete(self):
        items = [{"item_code": "Raw Item-1", "warehouse": "Stores - QL", "qty": 20,
                  "current_qty": 5, "valuation_rate": 2}]
        out = render_reconciliation_draft("Qkeee LLP", "2026-08-10", items)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("current_valuation_rate is missing", out)

    def test_valuation_rate_zero_does_not_require_current_rate(self):
        items = [{"item_code": "Raw Item-1", "warehouse": "Stores - QL", "qty": 20,
                  "current_qty": 5, "valuation_rate": 0}]
        out = render_reconciliation_draft("Qkeee LLP", "2026-08-10", items)
        self.assertIn("READY", out)


if __name__ == "__main__":
    unittest.main()
