#!/usr/bin/env python3
"""Unit tests for render_cancel_confirmation.py. Run: python scripts/test_render_cancel_confirmation.py"""

import unittest

from render_cancel_confirmation import RenderError, render_cancel_confirmation


class TestCancelConfirmationGate(unittest.TestCase):
    def test_refuses_empty_impact(self):
        with self.assertRaisesRegex(RenderError, "No impact stated"):
            render_cancel_confirmation("Journal Entry", "ACC-JV-2026-00001", "wrong cost center", impact=[])

    def test_refuses_missing_reason(self):
        with self.assertRaises(RenderError):
            render_cancel_confirmation("Journal Entry", "ACC-JV-2026-00001", "",
                                        impact=[{"label": "Cash - QL", "value": "debit 100 reversed"}])

    def test_accepts_well_formed_confirmation(self):
        out = render_cancel_confirmation(
            "Journal Entry", "ACC-JV-2026-00001", "wrong cost center used",
            impact=[
                {"label": "Cash - QL", "value": "debit 100 will be reversed"},
                {"label": "Outstanding effect", "value": "none - no invoice reopened"},
            ],
        )
        self.assertIn("NOT CANCELLED YET", out)
        self.assertIn("Cash - QL", out)
        self.assertIn("LinkExistsError", out)

    def test_no_downstream_effect_is_a_valid_stated_impact(self):
        # A cancel with genuinely nothing material changing must still say
        # so explicitly - "no effect" is itself a required statement, not
        # an excuse to pass an empty impact list.
        out = render_cancel_confirmation(
            "Journal Entry", "ACC-JV-2026-00002", "duplicate entry",
            impact=[{"label": "Financial effect", "value": "none - draft was never submitted"}],
        )
        self.assertIn("none - draft was never submitted", out)


if __name__ == "__main__":
    unittest.main()
