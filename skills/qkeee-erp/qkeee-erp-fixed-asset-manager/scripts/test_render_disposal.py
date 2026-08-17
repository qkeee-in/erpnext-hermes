#!/usr/bin/env python3
import unittest

from render_disposal import render_disposal, RenderError


class TestRenderDisposal(unittest.TestCase):
    def test_unknown_method_raises(self):
        with self.assertRaises(RenderError):
            render_disposal("ACC-ASS-1", "Laptop", "donate", "2026-08-10", 500, reason="x")

    def test_missing_reason_raises(self):
        with self.assertRaises(RenderError):
            render_disposal("ACC-ASS-1", "Laptop", "scrap", "2026-08-10", 500, reason="")

    def test_sale_without_proceeds_raises(self):
        with self.assertRaises(RenderError):
            render_disposal("ACC-ASS-1", "Laptop", "sale", "2026-08-10", 500, reason="EOL")

    def test_scrap_states_full_writeoff(self):
        out = render_disposal("ACC-ASS-1", "Laptop", "scrap", "2026-08-10", 500, reason="damaged")
        self.assertIn("full remaining book value", out)
        self.assertIn("500.00", out)
        self.assertIn("NOT DISPOSED YET", out)

    def test_sale_computes_gain(self):
        out = render_disposal("ACC-ASS-1", "Laptop", "sale", "2026-08-10", 500,
                               sale_proceeds=700, reason="replaced")
        self.assertIn("Estimated GAIN:** 200.00", out)

    def test_sale_computes_loss(self):
        out = render_disposal("ACC-ASS-1", "Laptop", "sale", "2026-08-10", 500,
                               sale_proceeds=300, reason="replaced")
        self.assertIn("Estimated LOSS:** 200.00", out)

    def test_sale_notes_no_auto_submit(self):
        out = render_disposal("ACC-ASS-1", "Laptop", "sale", "2026-08-10", 500,
                               sale_proceeds=500, reason="replaced")
        self.assertIn("does NOT do automatically", out)

    def test_wrong_book_value_source_raises(self):
        with self.assertRaises(RenderError):
            render_disposal("ACC-ASS-1", "Laptop", "scrap", "2026-08-10", 500, reason="damaged",
                             book_value_source="top_level")

    def test_emits_confirmation_token(self):
        out = render_disposal("ACC-ASS-1", "Laptop", "scrap", "2026-08-10", 500, reason="damaged")
        self.assertIn("Confirmation token:", out)


if __name__ == "__main__":
    unittest.main()
