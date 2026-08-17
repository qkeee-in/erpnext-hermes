#!/usr/bin/env python3
import re
import unittest

from render_config_change import RenderError, render_config_change

FIXED_TIME = 1_700_000_000


def _extract_token(rendered: str) -> str:
    m = re.search(r"Confirmation token:\*\* `([0-9a-f]+)`", rendered)
    assert m, f"no confirmation token found in:\n{rendered}"
    return m.group(1)


class TestRenderConfigChange(unittest.TestCase):
    def test_requires_reason(self):
        with self.assertRaises(RenderError):
            render_config_change(kind="create_webhook", doctype="Webhook",
                                  identifier="https://example.com/hook", reason="")

    def test_requires_identifier(self):
        with self.assertRaises(RenderError):
            render_config_change(kind="create_webhook", doctype="Webhook",
                                  identifier="", reason="sync data")

    def test_rejects_unknown_kind(self):
        with self.assertRaises(RenderError):
            render_config_change(kind="delete_everything", doctype="Webhook",
                                  identifier="x", reason="y")

    def test_webhook_warns_outbound_data(self):
        out = render_config_change(kind="create_webhook", doctype="Webhook",
                                    identifier="https://example.com/hook",
                                    reason="sync to billing system")
        self.assertIn("outbound data destination", out)
        self.assertIn("https://example.com/hook", out)

    def test_workflow_warns_approval_impact(self):
        out = render_config_change(kind="toggle_workflow", doctype="Workflow",
                                    identifier="Leave Application",
                                    reason="pause approvals during migration")
        self.assertIn("bypass approval steps", out)
        self.assertIn("Leave Application", out)

    def test_token_and_issued_at_present(self):
        out = render_config_change(kind="create_webhook", doctype="Webhook",
                                    identifier="https://example.com/hook",
                                    reason="sync data", issued_at=FIXED_TIME)
        self.assertIn("Confirmation token:", out)
        self.assertIn(f"Issued at:** {FIXED_TIME}", out)

    def test_token_stable_for_same_facts(self):
        out1 = render_config_change(kind="create_webhook", doctype="Webhook",
                                     identifier="https://example.com/hook",
                                     reason="sync data", issued_at=FIXED_TIME)
        out2 = render_config_change(kind="create_webhook", doctype="Webhook",
                                     identifier="https://example.com/hook",
                                     reason="sync data", issued_at=FIXED_TIME)
        self.assertEqual(_extract_token(out1), _extract_token(out2))

    def test_token_differs_for_different_url(self):
        out1 = render_config_change(kind="create_webhook", doctype="Webhook",
                                     identifier="https://example.com/hook",
                                     reason="sync data", issued_at=FIXED_TIME)
        out2 = render_config_change(kind="create_webhook", doctype="Webhook",
                                     identifier="https://evil.example.net/hook",
                                     reason="sync data", issued_at=FIXED_TIME)
        self.assertNotEqual(_extract_token(out1), _extract_token(out2))


if __name__ == "__main__":
    unittest.main()
