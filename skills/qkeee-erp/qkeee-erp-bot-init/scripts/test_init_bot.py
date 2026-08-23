#!/usr/bin/env python3
"""
Unit tests for init_bot.py's plan/confirm-token flow. Run:
    python scripts/test_init_bot.py

Mocks erp_client.resource_exists()/get_resource()/health_check()/
mutate_resource()/ensure_persona_registered()/ensure_qkeee_env_file_skeleton()
— does not hit a real ERPNext instance or touch the real filesystem.
"""

import time
import unittest
from unittest import mock

import erp_client
import init_bot
from confirm_token import full_init_plan_token
from doctype_defs import ALL_DOCTYPES, PERSONA_MANIFEST, ROLE_NAME

ALL_PERSONA_CODES = [p["persona_code"] for p in PERSONA_MANIFEST]


def _mock_resource_exists(missing_doctypes=(), role_missing=False, missing_personas=()):
    def fake(tag, doctype, name):
        if doctype == "Role":
            return not role_missing
        if doctype == "Qkeee Bot Persona" and name in ALL_PERSONA_CODES:
            return name not in missing_personas
        return name not in missing_doctypes
    return fake


# ensure_personas() calls the real ensure_persona_registered() unless
# mocked — that function's own get_env_config() call would otherwise hit
# a real (missing) env var in this test environment. Patched to a no-op
# "already_registered" stub everywhere run_real() actually executes the
# personas/env-file steps, matching a target where everything's already
# provisioned unless a test explicitly cares about persona creation.
def _noop_persona_status(tag, *, persona_code, **kwargs):
    return "already_registered"


class TestComputePlan(unittest.TestCase):
    def test_nothing_needed_when_all_exist(self):
        with mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists()):
            plan = init_bot.compute_plan("qa")
        self.assertEqual(plan, {
            "role_needed": False, "doctypes_needed": [], "personas_needed": [],
            "bot_email": None, "user_needed": False, "user_role_needed": False,
            "enable_needed": False, "keys_needed": False,
        })

    def test_reports_missing_role_and_doctypes(self):
        missing = {"Qkeee Bot Persona", "Qkeee Bot Audit Log"}
        with mock.patch("erp_client.resource_exists",
                         side_effect=_mock_resource_exists(missing_doctypes=missing, role_missing=True)):
            plan = init_bot.compute_plan("qa")
        self.assertTrue(plan["role_needed"])
        self.assertEqual(set(plan["doctypes_needed"]), missing)
        self.assertEqual(plan["personas_needed"], [])

    def test_reports_missing_personas(self):
        missing_personas = {"qkeee-erp-sales", "qkeee-erp-hr-associate"}
        with mock.patch("erp_client.resource_exists",
                         side_effect=_mock_resource_exists(missing_personas=missing_personas)):
            plan = init_bot.compute_plan("qa")
        self.assertEqual(set(plan["personas_needed"]), missing_personas)


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
        # role_missing=True with nothing else missing means only the role
        # is actually missing here — doctypes_needed/personas_needed empty.
        expected = full_init_plan_token("qa", "admin@org.com", True, [], [], issued_at=result["issued_at"])
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
            token = full_init_plan_token("qa", "admin@org.com", True,
                                          [d["name"] for d in ALL_DOCTYPES], [], issued_at=stale_issued_at)
            with self.assertRaises(erp_client.ConnectorError) as ctx:
                init_bot.run_real("qa", "admin@org.com", confirm_token=token, issued_at=stale_issued_at)
        self.assertIn("stale", str(ctx.exception))

    def test_refuses_token_computed_for_different_plan(self):
        # Token was issued when only the role was missing; target state
        # now also needs a doctype — plan changed, token must not match.
        issued_at = int(time.time())
        stale_token = full_init_plan_token("qa", "admin@org.com", True, [], [], issued_at=issued_at)
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
                mock.patch("erp_client.mutate_resource", return_value={"data": {"name": ROLE_NAME}}) as mocked_mutate, \
                mock.patch("erp_client.ensure_persona_registered", side_effect=_noop_persona_status), \
                mock.patch("erp_client.ensure_qkeee_env_file_skeleton", return_value=False):
            token = full_init_plan_token("qa", "admin@org.com", True, [], [], issued_at=issued_at)
            summary = init_bot.run_real("qa", "admin@org.com", confirm_token=token, issued_at=issued_at)
        self.assertTrue(summary["role_created"])
        # user_approved must be threaded through to the audit log, not left
        # at the connector's default of False/"Not Confirmed".
        for call in mocked_mutate.call_args_list:
            self.assertTrue(call.kwargs.get("user_approved"))

    def test_no_token_required_when_nothing_needed(self):
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists()), \
                mock.patch("erp_client.ensure_persona_registered", side_effect=_noop_persona_status), \
                mock.patch("erp_client.ensure_qkeee_env_file_skeleton", return_value=False):
            summary = init_bot.run_real("qa", "admin@org.com", confirm_token=None, issued_at=None)
        self.assertFalse(summary["role_created"])
        self.assertEqual(summary["doctypes_created"], [])


class TestEnsurePersonas(unittest.TestCase):
    def test_registers_every_manifest_persona(self):
        with mock.patch("erp_client.ensure_persona_registered", side_effect=_noop_persona_status) as mocked:
            results = init_bot.ensure_personas("qa", "admin@org.com")
        self.assertEqual(mocked.call_count, len(PERSONA_MANIFEST))
        self.assertEqual(set(results.keys()), set(ALL_PERSONA_CODES))
        self.assertTrue(all(status == "already_registered" for status in results.values()))


class TestBotUserWiring(unittest.TestCase):
    """init_bot.py --bot-email folds the bot-user plan into the SAME
    combined dry-run/confirm-token round trip as role/doctypes/personas."""

    def test_dry_run_includes_bot_user_plan(self):
        user_plan = {"bot_email": "bot@org.com", "user_exists": False, "user_needed": True,
                     "role_needed": True, "enable_needed": False, "keys_needed": True}
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists()), \
                mock.patch("ensure_bot_user.compute_plan", return_value=user_plan) as mocked_plan:
            result = init_bot.run_dry_run("qa", "admin@org.com", bot_email="bot@org.com")
        mocked_plan.assert_called_once_with("qa", "bot@org.com", require_role_exists=False)
        self.assertTrue(result["user_needed"])
        self.assertTrue(result["keys_needed"])
        self.assertIsNotNone(result["confirm_token"])

    def test_real_run_creates_bot_user_and_env_skeleton(self):
        user_plan = {"bot_email": "bot@org.com", "user_exists": False, "user_needed": True,
                     "role_needed": True, "enable_needed": False, "keys_needed": True}
        issued_at = int(time.time())
        with mock.patch("erp_client.health_check", return_value={"status": "ok"}), \
                mock.patch("erp_client.resource_exists", side_effect=_mock_resource_exists()), \
                mock.patch("ensure_bot_user.compute_plan", return_value=user_plan), \
                mock.patch("erp_client.mutate_resource", return_value={"data": {}}), \
                mock.patch("erp_client.get_env_config", return_value={"tag": "qa"}), \
                mock.patch("erp_client._request",
                            return_value={"message": {"api_key": "k", "api_secret": "s"}}), \
                mock.patch("erp_client.ensure_persona_registered", side_effect=_noop_persona_status), \
                mock.patch("erp_client.ensure_qkeee_env_file_skeleton", return_value=True) as mocked_skeleton:
            token = full_init_plan_token(
                "qa", "admin@org.com", False, [], [], bot_email="bot@org.com",
                user_needed=True, user_role_needed=True, enable_needed=False, keys_needed=True,
                issued_at=issued_at,
            )
            summary = init_bot.run_real("qa", "admin@org.com", confirm_token=token, issued_at=issued_at,
                                         bot_email="bot@org.com")
        self.assertTrue(summary["bot_user"]["user_created"])
        self.assertTrue(summary["qkeee_env_file_created"])
        # skeleton is called once for the plain provisioning step and once
        # more inside the key-print step (both idempotent/no-op on a real
        # target once the file exists) — just confirm it ran.
        self.assertTrue(mocked_skeleton.called)


if __name__ == "__main__":
    unittest.main()
