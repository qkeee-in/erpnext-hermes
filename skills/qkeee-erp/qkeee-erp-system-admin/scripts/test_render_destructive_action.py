#!/usr/bin/env python3
import unittest

from render_destructive_action import RenderError, render_destructive_action


class TestRenderDestructiveAction(unittest.TestCase):
    def test_requires_reason(self):
        with self.assertRaises(RenderError):
            render_destructive_action(action="disable_user", target_name="a@b.com", reason="")

    def test_requires_target_name(self):
        with self.assertRaises(RenderError):
            render_destructive_action(action="disable_user", target_name="", reason="left the org")

    def test_bad_action_rejected(self):
        with self.assertRaises(RenderError):
            render_destructive_action(action="nuke_everything", target_name="x", reason="y")

    def test_disable_user_says_reversible(self):
        out = render_destructive_action(action="disable_user", target_name="a@b.com",
                                         reason="left the org")
        self.assertIn("reversible", out.lower())
        self.assertIn("DISABLE", out)

    def test_delete_user_says_permanent(self):
        out = render_destructive_action(action="delete_user", target_name="a@b.com",
                                         reason="never used, created by mistake")
        self.assertIn("PERMANENTLY DELETE", out)

    def test_no_impact_notes_warns(self):
        out = render_destructive_action(action="delete_custom_field",
                                         target_name="ToDo-qkeee_test", reason="unused")
        self.assertIn("No impact_notes supplied", out)

    def test_impact_notes_surfaced(self):
        out = render_destructive_action(action="delete_custom_field",
                                         target_name="ToDo-qkeee_test", reason="unused",
                                         impact_notes="Referenced in 2 Print Formats.")
        self.assertIn("Referenced in 2 Print Formats.", out)

    def test_token_and_issued_at_present(self):
        out = render_destructive_action(action="delete_webhook", target_name="WH-001",
                                         reason="misconfigured, replaced", issued_at=1_700_000_000)
        self.assertIn("Confirmation token:", out)
        self.assertIn("Issued at:** 1700000000", out)

    def test_mentions_audit_comment(self):
        out = render_destructive_action(action="disable_user", target_name="a@b.com",
                                         reason="left the org")
        self.assertIn("Comment", out)


if __name__ == "__main__":
    unittest.main()
