#!/usr/bin/env python3
import unittest

from render_stock_entry_draft import render_stock_entry_draft, RenderError


class TestRenderStockEntryDraft(unittest.TestCase):
    def test_unknown_purpose_raises(self):
        with self.assertRaises(RenderError):
            render_stock_entry_draft("Teleport", "Qkeee LLP", [{"item_code": "SKU-1", "qty": 1}], {})

    def test_empty_items_raises(self):
        with self.assertRaises(RenderError):
            render_stock_entry_draft("Material Transfer", "Qkeee LLP", [], {})

    def test_item_missing_required_key_raises(self):
        with self.assertRaises(RenderError):
            render_stock_entry_draft("Material Transfer", "Qkeee LLP", [{"item_code": "SKU-1"}], {})

    def test_ready_receipt_no_source_needed(self):
        items = [{"item_code": "SKU-1", "qty": 10, "t_warehouse": "Stores - QL", "valuation_rate": 5}]
        out = render_stock_entry_draft("Material Receipt", "Qkeee LLP", items, {})
        self.assertIn("READY", out)

    def test_incomplete_when_no_source_qty_provided(self):
        items = [{"item_code": "SKU-1", "qty": 4, "s_warehouse": "Stores - QL", "t_warehouse": "Finished Goods - QL"}]
        out = render_stock_entry_draft("Material Transfer", "Qkeee LLP", items, {})
        self.assertIn("INCOMPLETE", out)
        self.assertIn("no actual_source_qty entry", out)

    def test_incomplete_when_qty_exceeds_available(self):
        items = [{"item_code": "SKU-1", "qty": 999, "s_warehouse": "Stores - QL", "t_warehouse": "Finished Goods - QL"}]
        out = render_stock_entry_draft(
            "Material Transfer", "Qkeee LLP", items, {("SKU-1", "Stores - QL"): 6}
        )
        self.assertIn("INCOMPLETE", out)
        self.assertIn("exceeds actual", out)

    def test_ready_when_qty_within_available(self):
        items = [{"item_code": "SKU-1", "qty": 4, "s_warehouse": "Stores - QL", "t_warehouse": "Finished Goods - QL"}]
        out = render_stock_entry_draft(
            "Material Transfer", "Qkeee LLP", items, {("SKU-1", "Stores - QL"): 6}
        )
        self.assertIn("READY", out)

    def test_string_key_form_accepted(self):
        items = [{"item_code": "SKU-1", "qty": 4, "s_warehouse": "Stores - QL", "t_warehouse": "Finished Goods - QL"}]
        out = render_stock_entry_draft(
            "Material Transfer", "Qkeee LLP", items, {"SKU-1|Stores - QL": 6}
        )
        self.assertIn("READY", out)

    def test_line_with_no_warehouse_at_all_is_incomplete(self):
        items = [{"item_code": "SKU-1", "qty": 4}]
        out = render_stock_entry_draft("Material Issue", "Qkeee LLP", items, {})
        self.assertIn("INCOMPLETE", out)
        self.assertIn("must move stock somewhere", out)

    def test_two_lines_same_source_individually_ok_combined_overdraw_is_incomplete(self):
        # Each line (5) is individually under the 6.0 balance, but combined (10) is not.
        items = [
            {"item_code": "SKU-1", "qty": 5, "s_warehouse": "Stores - QL", "t_warehouse": "Finished Goods - QL"},
            {"item_code": "SKU-1", "qty": 5, "s_warehouse": "Stores - QL", "t_warehouse": "Finished Goods - QL"},
        ]
        out = render_stock_entry_draft(
            "Material Transfer", "Qkeee LLP", items, {("SKU-1", "Stores - QL"): 6}
        )
        self.assertIn("INCOMPLETE", out)
        self.assertIn("already claimed by an earlier line", out)

    def test_two_lines_same_source_within_combined_balance_is_ready(self):
        items = [
            {"item_code": "SKU-1", "qty": 2, "s_warehouse": "Stores - QL", "t_warehouse": "Finished Goods - QL"},
            {"item_code": "SKU-1", "qty": 3, "s_warehouse": "Stores - QL", "t_warehouse": "Finished Goods - QL"},
        ]
        out = render_stock_entry_draft(
            "Material Transfer", "Qkeee LLP", items, {("SKU-1", "Stores - QL"): 6}
        )
        self.assertIn("READY", out)

    def test_zero_qty_is_incomplete(self):
        items = [{"item_code": "SKU-1", "qty": 0, "t_warehouse": "Stores - QL"}]
        out = render_stock_entry_draft("Material Receipt", "Qkeee LLP", items, {})
        self.assertIn("INCOMPLETE", out)
        self.assertIn("must be greater than zero", out)

    def test_negative_qty_is_incomplete(self):
        items = [{"item_code": "SKU-1", "qty": -4, "t_warehouse": "Stores - QL"}]
        out = render_stock_entry_draft("Material Receipt", "Qkeee LLP", items, {})
        self.assertIn("INCOMPLETE", out)
        self.assertIn("must be greater than zero", out)


if __name__ == "__main__":
    unittest.main()
