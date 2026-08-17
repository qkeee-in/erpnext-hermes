#!/usr/bin/env python3
import re
import unittest

from render_user_draft import RenderError, render_user_draft

FIXED_TIME = 1_700_000_000


def _extract_elevated_token(rendered: str) -> str:
    m = re.search(r"Elevated-role confirmation token:\*\* `([0-9a-f]+)`", rendered)
    assert m, f"no elevated-role token found in:\n{rendered}"
    return m.group(1)


class TestRenderUserDraft(unittest.TestCase):
    def test_requires_valid_email(self):
        with self.assertRaises(RenderError):
            render_user_draft(email="not-an-email", first_name="A", roles=["Auditor"])

    def test_requires_first_name(self):
        with self.assertRaises(RenderError):
            render_user_draft(email="a@b.com", first_name="", roles=["Auditor"])

    def test_requires_nonempty_roles(self):
        with self.assertRaises(RenderError):
            render_user_draft(email="a@b.com", first_name="A", roles=[])

    def test_unknown_role_blocks(self):
        out = render_user_draft(email="a@b.com", first_name="A", roles=["Bogus Role"],
                                 existing_roles=["Auditor", "HR User"])
        self.assertIn("BLOCKED", out)
        self.assertIn("Ready to create:** NO", out)

    def test_known_roles_ready(self):
        out = render_user_draft(email="a@b.com", first_name="A", roles=["HR User"],
                                 existing_roles=["Auditor", "HR User"])
        self.assertIn("Ready to create:** YES", out)

    def test_elevated_role_blocks_without_ack(self):
        out = render_user_draft(email="a@b.com", first_name="A", roles=["System Manager"],
                                 existing_roles=["System Manager"])
        self.assertIn("ELEVATED ROLE", out)
        self.assertIn("Ready to create:** NO", out)

    def test_elevated_role_ready_with_ack(self):
        out = render_user_draft(email="a@b.com", first_name="A", roles=["System Manager"],
                                 existing_roles=["System Manager"],
                                 elevated_roles_acknowledged=True)
        self.assertIn("Ready to create:** YES", out)

    def test_administrator_role_is_elevated(self):
        out = render_user_draft(email="a@b.com", first_name="A", roles=["Administrator"],
                                 existing_roles=["Administrator"])
        self.assertIn("ELEVATED ROLE", out)

    def test_elevated_role_emits_confirmation_token(self):
        out = render_user_draft(email="a@b.com", first_name="A", roles=["System Manager"],
                                 existing_roles=["System Manager"],
                                 elevated_roles_acknowledged=True, issued_at=FIXED_TIME)
        self.assertIn("Elevated-role confirmation token:", out)
        self.assertIn(f"Issued at:** {FIXED_TIME}", out)

    def test_non_elevated_role_has_no_confirmation_token(self):
        out = render_user_draft(email="a@b.com", first_name="A", roles=["HR User"],
                                 existing_roles=["HR User"])
        self.assertNotIn("confirmation token", out.lower())

    def test_elevated_token_stable_for_same_facts(self):
        out1 = render_user_draft(email="a@b.com", first_name="A", roles=["System Manager"],
                                  existing_roles=["System Manager"], issued_at=FIXED_TIME)
        out2 = render_user_draft(email="a@b.com", first_name="A", roles=["System Manager"],
                                  existing_roles=["System Manager"], issued_at=FIXED_TIME)
        self.assertEqual(_extract_elevated_token(out1), _extract_elevated_token(out2))

    def test_elevated_token_emitted_even_without_ack(self):
        # BLOCKED (not ready) until acknowledged, but the token is still
        # printed so a caller can see what execute() will require next.
        out = render_user_draft(email="a@b.com", first_name="A", roles=["System Manager"],
                                 existing_roles=["System Manager"], issued_at=FIXED_TIME)
        self.assertIn("Ready to create:** NO", out)
        self.assertIn("Elevated-role confirmation token:", out)


if __name__ == "__main__":
    unittest.main()
