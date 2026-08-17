#!/usr/bin/env python3
"""
Unit tests for ensure_bot_user.py's plan/confirm-token flow. Run:
python scripts/test_ensure_bot_user.py

Mocks erp_client.resource_exists()/get_resource()/health_check()/
mutate_resource()/get_env_config()/_request() — does not hit a real
ERPNext instance.
"""

import time
import unittest
from unittest import mock

import erp_client
import ensure_bot_user as ebu
from confirm_token import bot_user_plan_token
from doctype_defs import ROLE_NAME

BOT_EMAIL = "qkeee-erp-bot@org.com"


def _resource_exists(role_exists=True, user_exists=False):
    def fake(tag, doctype, name):
        if doctype == "Role":
            return role_exists
        if doctype == "User":
            return user_exists
        raise AssertionError(f"unexpected doctype {doctype}")
    return fake


class TestComputePlan(unittest.TestCase):
    def test_raises_when_role_missing(self):
        with mock.patch("erp_client.resource_exists", side_effect=_resource_exists(role_exists=False)):
            with self.assertRaises(erp_client.ConnectorError) as ctx:
                ebu.compute_plan("qa", BOT_EMAIL)
        self.assertIn("init_bot.py", str(ctx.exception))

    def test_user_needed_when_absent(self):
        with mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=False)):
            plan = ebu.compute_plan("qa", BOT_EMAIL)
        self.assertTrue(plan["user_needed"])
        self.assertTrue(plan["role_needed"])
        self.assertTrue(plan["keys_needed"])
        self.assertFalse(plan["enable_needed"])

    def test_existing_user_with_role_and_keys_needs_nothing(self):
        user_doc = {"roles": [{"role": ROLE_NAME}], "enabled": 1, "api_key": "abc123"}
        with mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=True)), \
                mock.patch("erp_client.get_resource", return_value={"data": user_doc}):
            plan = ebu.compute_plan("qa", BOT_EMAIL)
        self.assertFalse(plan["user_needed"])
        self.assertFalse(plan["role_needed"])
        self.assertFalse(plan["enable_needed"])
        self.assertFalse(plan["keys_needed"])

    def test_existing_user_missing_role_and_disabled(self):
        user_doc = {"roles": [], "enabled": 0, "api_key": "abc123"}
        with mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=True)), \
                mock.patch("erp_client.get_resource", return_value={"data": user_doc}):
            plan = ebu.compute_plan("qa", BOT_EMAIL)
        self.assertFalse(plan["user_needed"])
        self.assertTrue(plan["role_needed"])
        self.assertTrue(plan["enable_needed"])
        self.assertFalse(plan["keys_needed"])


class TestRunDryRun(unittest.TestCase):
    def test_no_token_when_nothing_needed(self):
        user_doc = {"roles": [{"role": ROLE_NAME}], "enabled": 1, "api_key": "abc123"}
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=True)), \
                mock.patch("erp_client.get_resource", return_value={"data": user_doc}):
            result = ebu.run_dry_run("qa", BOT_EMAIL, "admin@org.com")
        self.assertIsNone(result["confirm_token"])

    def test_issues_token_when_user_missing(self):
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=False)):
            result = ebu.run_dry_run("qa", BOT_EMAIL, "admin@org.com")
        self.assertIsNotNone(result["confirm_token"])
        expected = bot_user_plan_token("qa", "admin@org.com", BOT_EMAIL, True, True, False, True,
                                        result["issued_at"])
        self.assertEqual(result["confirm_token"], expected)


class TestRunRealTokenGate(unittest.TestCase):
    def test_refuses_without_token_when_user_missing(self):
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=False)):
            with self.assertRaises(erp_client.ConnectorError) as ctx:
                ebu.run_real("qa", BOT_EMAIL, "admin@org.com", confirm_token=None, issued_at=None)
        self.assertIn("--dry-run", str(ctx.exception))

    def test_refuses_stale_token(self):
        stale_issued_at = int(time.time()) - 10_000
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=False)):
            token = bot_user_plan_token("qa", "admin@org.com", BOT_EMAIL, True, True, False, True,
                                         stale_issued_at)
            with self.assertRaises(erp_client.ConnectorError) as ctx:
                ebu.run_real("qa", BOT_EMAIL, "admin@org.com", confirm_token=token, issued_at=stale_issued_at)
        self.assertIn("stale", str(ctx.exception))

    def test_refuses_mismatched_plan(self):
        issued_at = int(time.time())
        # Token issued for "keys already present" but live state now needs keys too.
        stale_token = bot_user_plan_token("qa", "admin@org.com", BOT_EMAIL, True, True, False, False,
                                           issued_at)
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=False)):
            with self.assertRaises(erp_client.ConnectorError) as ctx:
                ebu.run_real("qa", BOT_EMAIL, "admin@org.com", confirm_token=stale_token, issued_at=issued_at)
        self.assertIn("does not match", str(ctx.exception))

    def test_creates_user_and_generates_keys_with_matching_token(self):
        issued_at = int(time.time())
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=False)), \
                mock.patch("erp_client.mutate_resource", return_value={"data": {"name": BOT_EMAIL}}) as mocked_mutate, \
                mock.patch("erp_client.get_env_config", return_value={"tag": "qa"}), \
                mock.patch("erp_client._request",
                            return_value={"message": {"api_key": "newkey", "api_secret": "newsecret"}}):
            token = bot_user_plan_token("qa", "admin@org.com", BOT_EMAIL, True, True, False, True, issued_at)
            result = ebu.run_real("qa", BOT_EMAIL, "admin@org.com", confirm_token=token, issued_at=issued_at)
        self.assertTrue(result["user_created"])
        self.assertEqual(result["api_key"], "newkey")
        self.assertEqual(result["api_secret"], "newsecret")
        mocked_mutate.assert_called_once()
        self.assertTrue(mocked_mutate.call_args.kwargs.get("user_approved"))

    def test_no_token_required_when_nothing_needed(self):
        user_doc = {"roles": [{"role": ROLE_NAME}], "enabled": 1, "api_key": "abc123"}
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_resource_exists(user_exists=True)), \
                mock.patch("erp_client.get_resource", return_value={"data": user_doc}):
            result = ebu.run_real("qa", BOT_EMAIL, "admin@org.com", confirm_token=None, issued_at=None)
        self.assertFalse(result["user_created"])
        self.assertIsNone(result["api_key"])


if __name__ == "__main__":
    unittest.main()
