#!/usr/bin/env python3
import re
import time
import unittest

from render_permission_change import RenderError, render_permission_change

FIXED_TIME = 1_700_000_000


def _extract_token(rendered: str) -> str:
    m = re.search(r"Confirmation token:\*\* `([0-9a-f]+)`", rendered)
    assert m, f"no confirmation token found in:\n{rendered}"
    return m.group(1)


class TestRenderPermissionChange(unittest.TestCase):
    def test_requires_reason(self):
        with self.assertRaises(RenderError):
            render_permission_change(action="add", doctype="Contact", role="Auditor", reason="")

    def test_add_requires_role(self):
        with self.assertRaises(RenderError):
            render_permission_change(action="add", doctype="Contact", role="", reason="onboarding")

    def test_reset_does_not_require_role(self):
        out = render_permission_change(action="reset", doctype="Contact", reason="undo bad customization")
        self.assertIn("WIDEST BLAST RADIUS", out)
        self.assertIn("Confirmation token:", out)

    def test_update_requires_ptype_and_value(self):
        with self.assertRaises(RenderError):
            render_permission_change(action="update", doctype="Contact", role="Auditor",
                                      reason="grant write")

    def test_update_rejects_bad_new_value(self):
        with self.assertRaises(RenderError):
            render_permission_change(action="update", doctype="Contact", role="Auditor",
                                      ptype="write", new_value=2, reason="grant write")

    def test_update_states_grant_vs_revoke(self):
        granting = render_permission_change(action="update", doctype="Contact", role="Auditor",
                                             ptype="write", current_value=0, new_value=1,
                                             reason="needs write access")
        self.assertIn("GRANTING", granting)
        revoking = render_permission_change(action="update", doctype="Contact", role="Auditor",
                                             ptype="write", current_value=1, new_value=0,
                                             reason="revoke access")
        self.assertIn("REVOKING", revoking)

    def test_remove_without_current_row_warns(self):
        out = render_permission_change(action="remove", doctype="Contact", role="Auditor",
                                        reason="no longer needed")
        self.assertIn("WARNING", out)

    def test_remove_warns_standard_row_may_survive(self):
        out = render_permission_change(action="remove", doctype="Contact", role="Auditor",
                                        current_row={"read": 1, "write": 1, "delete": 0},
                                        reason="no longer needed")
        self.assertIn("may not fully revoke access", out)

    def test_remove_with_current_row_lists_rights(self):
        out = render_permission_change(action="remove", doctype="Contact", role="Auditor",
                                        current_row={"read": 1, "write": 1, "delete": 0},
                                        reason="no longer needed")
        self.assertIn("read", out)
        self.assertIn("write", out)

    def test_token_and_issued_at_present(self):
        out = render_permission_change(action="update", doctype="Contact", role="Auditor",
                                        ptype="write", current_value=0, new_value=1,
                                        reason="needs write access", issued_at=FIXED_TIME)
        self.assertIn("Confirmation token:", out)
        self.assertIn(f"Issued at:** {FIXED_TIME}", out)

    def test_token_stable_for_same_facts_and_issued_at(self):
        out1 = render_permission_change(action="update", doctype="Contact", role="Auditor",
                                         ptype="write", current_value=0, new_value=1,
                                         reason="needs write access", issued_at=FIXED_TIME)
        out2 = render_permission_change(action="update", doctype="Contact", role="Auditor",
                                         ptype="write", current_value=0, new_value=1,
                                         reason="needs write access", issued_at=FIXED_TIME)
        self.assertEqual(_extract_token(out1), _extract_token(out2))

    def test_token_differs_across_issued_at(self):
        out1 = render_permission_change(action="update", doctype="Contact", role="Auditor",
                                         ptype="write", current_value=0, new_value=1,
                                         reason="needs write access", issued_at=FIXED_TIME)
        out2 = render_permission_change(action="update", doctype="Contact", role="Auditor",
                                         ptype="write", current_value=0, new_value=1,
                                         reason="needs write access", issued_at=FIXED_TIME + 1)
        self.assertNotEqual(_extract_token(out1), _extract_token(out2))

    def test_issued_at_defaults_to_now_when_omitted(self):
        before = int(time.time())
        out = render_permission_change(action="add", doctype="Contact", role="Auditor",
                                        reason="onboarding")
        after = int(time.time())
        printed = int(out.split("Issued at:** ")[1].split(" ")[0])
        self.assertTrue(before <= printed <= after)

    def test_bad_action_rejected(self):
        with self.assertRaises(RenderError):
            render_permission_change(action="grant_all", doctype="Contact", role="Auditor",
                                      reason="x")


if __name__ == "__main__":
    unittest.main()
