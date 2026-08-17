#!/usr/bin/env python3
import unittest

from render_material_request_draft import render_material_request_draft, RenderError


class TestRenderMaterialRequestDraft(unittest.TestCase):
    def test_unknown_type_raises(self):
        with self.assertRaises(RenderError):
            render_material_request_draft("Teleport", "Qkeee LLP", "2026-08-10",
                                           [{"item_code": "Raw Item-2", "qty": 50, "schedule_date": "2026-08-20"}])

    def test_empty_items_raises(self):
        with self.assertRaises(RenderError):
            render_material_request_draft("Purchase", "Qkeee LLP", "2026-08-10", [])

    def test_incomplete_when_missing_required_fields(self):
        out = render_material_request_draft("Purchase", "Qkeee LLP", "2026-08-10",
                                             [{"item_code": "Raw Item-2"}])
        self.assertIn("INCOMPLETE", out)

    def test_incomplete_purchase_without_warehouse(self):
        items = [{"item_code": "Raw Item-2", "qty": 50, "schedule_date": "2026-08-20"}]
        out = render_material_request_draft("Purchase", "Qkeee LLP", "2026-08-10", items)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("no target warehouse stated", out)

    def test_ready_purchase_with_warehouse(self):
        items = [{"item_code": "Raw Item-2", "qty": 50, "schedule_date": "2026-08-20", "warehouse": "Stores - QL"}]
        out = render_material_request_draft("Purchase", "Qkeee LLP", "2026-08-10", items)
        self.assertIn("READY", out)

    def test_ready_material_transfer_without_warehouse(self):
        items = [{"item_code": "Raw Item-2", "qty": 50, "schedule_date": "2026-08-20"}]
        out = render_material_request_draft("Material Transfer", "Qkeee LLP", "2026-08-10", items)
        self.assertIn("READY", out)

    def test_negative_qty_is_incomplete(self):
        # qty: 0 is already caught by the "missing required field" check (falsy);
        # a negative qty is not falsy and needs its own explicit guard.
        items = [{"item_code": "Raw Item-2", "qty": -5, "schedule_date": "2026-08-20", "warehouse": "Stores - QL"}]
        out = render_material_request_draft("Purchase", "Qkeee LLP", "2026-08-10", items)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("must be greater than zero", out)


if __name__ == "__main__":
    unittest.main()
