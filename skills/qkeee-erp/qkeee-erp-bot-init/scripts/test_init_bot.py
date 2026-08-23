#!/usr/bin/env python3
"""
Unit tests for init_bot.py's plan/confirm-token flow. Run:
    python scripts/test_init_bot.py

Mocks erp_client.resource_exists()/get_resource()/health_check()/
mutate_resource() — does not hit a real ERPNext instance.
"""

import time
import unittest
from unittest import mock

import erp_client
import init_bot
from confirm_token import init_plan_token
from doctype_defs import ALL_DOCTYPES, ROLE_NAME


def _mock_resource_exists(missing_doctypes=(), role_missing=False):
    def fake(tag, doctype, name):
        if doctype == "Role":
            return not role_missing
        return name not in missing_doctypes
    return fake


class TestComputePlan(unittest.TestCase):
    def test_nothing_needed_when_all_exist(self):
        with mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists()):
            plan = init_bot.compute_plan("qa")
        self.assertEqual(plan, {"role_needed": False, "doctypes_needed": []})

    def test_reports_missing_role_and_doctypes(self):
        missing = {"Qkeee Bot Persona", "Qkeee Bot Audit Log"}
        with mock.patch("erp_client.resource_exists",
                         side_effect=_mock_resource_exists(missing_doctypes=missing, role_missing=True)):
            plan = init_bot.compute_plan("qa")
        self.assertTrue(plan["role_needed"])
        self.assertEqual(set(plan["doctypes_needed"]), missing)


class TestRunDryRun(unittest.TestCase):
    def test_prints_no_token_when_nothing_to_do(self):
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists()):
            result = init_bot.run_dry_run("qa", "admin@org.com")
        self.assertIsNone(result["confirm_token"])
        self.assertIsNone(result["issued_at"])

    def test_issues_token_when_something_needed(self):
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists",
                            side_effect=_mock_resource_exists(role_missing=True)):
            result = init_bot.run_dry_run("qa", "admin@org.com")
        self.assertIsNotNone(result["confirm_token"])
        self.assertIsNotNone(result["issued_at"])
        # role_missing=True with no missing_doctypes means only the role is
        # actually missing here — doctypes_needed is empty.
        expected = init_plan_token("qa", "admin@org.com", True, [], result["issued_at"])
        self.assertEqual(result["confirm_token"], expected)


class TestRunRealTokenGate(unittest.TestCase):
    def test_refuses_without_token_when_something_needed(self):
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists(role_missing=True)):
            with self.assertRaises(erp_client.ConnectorError) as ctx:
                init_bot.run_real("qa", "admin@org.com", confirm_token=None, issued_at=None)
        self.assertIn("--dry-run", str(ctx.exception))

    def test_refuses_stale_token(self):
        stale_issued_at = int(time.time()) - 10_000  # well past the 900s TTL
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists(role_missing=True)):
            token = init_plan_token("qa", "admin@org.com", True,
                                     [d["name"] for d in ALL_DOCTYPES], stale_issued_at)
            with self.assertRaises(erp_client.ConnectorError) as ctx:
                init_bot.run_real("qa", "admin@org.com", confirm_token=token, issued_at=stale_issued_at)
        self.assertIn("stale", str(ctx.exception))

    def test_refuses_token_computed_for_different_plan(self):
        # Token was issued when only the role was missing; target state
        # now also needs a doctype — plan changed, token must not match.
        issued_at = int(time.time())
        stale_token = init_plan_token("qa", "admin@org.com", True, [], issued_at)
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists",
                            side_effect=_mock_resource_exists(missing_doctypes={"Qkeee Bot Persona"},
                                                               role_missing=True)):
            with self.assertRaises(erp_client.ConnectorError) as ctx:
                init_bot.run_real("qa", "admin@org.com", confirm_token=stale_token, issued_at=issued_at)
        self.assertIn("does not match", str(ctx.exception))

    def test_proceeds_with_matching_fresh_token(self):
        issued_at = int(time.time())
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists(role_missing=True)), \
                mock.patch("erp_client.mutate_resource", return_value={"data": {"name": ROLE_NAME}}) as mocked_mutate:
            token = init_plan_token("qa", "admin@org.com", True, [], issued_at)
            summary = init_bot.run_real("qa", "admin@org.com", confirm_token=token, issued_at=issued_at)
        self.assertTrue(summary["role_created"])
        # user_approved must be threaded through to the audit log, not left
        # at the connector's default of False/"Not Confirmed".
        for call in mocked_mutate.call_args_list:
            self.assertTrue(call.kwargs.get("user_approved"))

    def test_no_token_required_when_nothing_needed(self):
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists()):
            summary = init_bot.run_real("qa", "admin@org.com", confirm_token=None, issued_at=None)
        self.assertFalse(summary["role_created"])
        self.assertEqual(summary["doctypes_created"], [])


if __name__ == "__main__":
    unittest.main()
