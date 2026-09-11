#!/usr/bin/env python3
"""Regression tests for init_bot.py's log_role_provisioning() — the
manual Qkeee Bot Audit Log entries for Role provisioning, added because
`Role` sits in core.client.AUDIT_EXEMPT_DOCTYPES and is never
auto-logged by the normal write path (see that function's own docstring
for why Role must be created before the Audit Log DocType, and why this
deliberately bypasses the exemption via the raw _audit_insert()/
_audit_submit() primitives rather than _log_read()/
record_audit_log_start(), both of which honor it and would silently
no-op for "Role"). Bare `import init_bot` matches this repo's convention
for a top-level scripts/ module with no __init__.py.

Not re-testing _audit_insert()/_audit_submit() themselves — core/
test_client.py already covers those."""

import time
import unittest
from unittest.mock import patch

import init_bot


class LogRoleProvisioningTests(unittest.TestCase):
    @patch.object(init_bot, "_audit_submit")
    @patch.object(init_bot, "_audit_insert", return_value="AUDITLOG-READ-0001")
    @patch.object(init_bot.core_client, "get_env_config", return_value={"tag": "qa"})
    def test_role_already_existed_logs_read_only(self, mocked_cfg, mocked_insert, mocked_submit):
        init_bot.log_role_provisioning("qa", "admin@org.com", role_created=False,
                                        approval_note="dry-run confirmed")
        mocked_insert.assert_called_once()
        fields = mocked_insert.call_args[0][1]
        self.assertEqual(fields["action"], "Read")
        self.assertEqual(fields["reference_doctype"], "Role")
        self.assertEqual(fields["reference_name"], init_bot.ROLE_NAME)
        self.assertEqual(fields["requested_by"], "admin@org.com")
        self.assertEqual(fields["status"], "Success")
        mocked_submit.assert_called_once_with({"tag": "qa"}, "AUDITLOG-READ-0001")

    @patch.object(init_bot, "_audit_submit")
    @patch.object(init_bot, "_audit_insert", side_effect=["AUDITLOG-READ-0002", "AUDITLOG-CREATE-0002"])
    @patch.object(init_bot.core_client, "get_env_config", return_value={"tag": "qa"})
    def test_role_created_this_run_logs_read_then_create(self, mocked_cfg, mocked_insert, mocked_submit):
        init_bot.log_role_provisioning("qa", "admin@org.com", role_created=True,
                                        approval_note="dry-run confirmed, token abc123...")
        self.assertEqual(mocked_insert.call_count, 2)
        read_fields, create_fields = (c[0][1] for c in mocked_insert.call_args_list)
        self.assertEqual(read_fields["action"], "Read")
        self.assertEqual(create_fields["action"], "Create")
        self.assertEqual(create_fields["user_approved"], "Approved")
        self.assertEqual(create_fields["approval_note"], "dry-run confirmed, token abc123...")
        self.assertEqual(create_fields["reference_name"], init_bot.ROLE_NAME)

    @patch.object(init_bot, "_audit_submit")
    @patch.object(init_bot, "_audit_insert", return_value=None)
    @patch.object(init_bot.core_client, "get_env_config", return_value={"tag": "qa"})
    def test_insert_failure_never_raises(self, mocked_cfg, mocked_insert, mocked_submit):
        # _audit_insert()'s own contract: None on failure, never raises —
        # this function must stay best-effort too, matching every other
        # audit-logging call site in this skill.
        init_bot.log_role_provisioning("qa", "admin@org.com", role_created=True,
                                        approval_note="note")  # must not raise


class RunRealCallsLogRoleProvisioningAfterDoctypesTests(unittest.TestCase):
    """Ordering matters: the Audit Log DocType must exist before a row
    can be written into it, so log_role_provisioning() must run AFTER
    the DocType-creation loop, not before/alongside Role creation."""

    @patch.object(init_bot, "log_role_provisioning")
    @patch.object(init_bot, "ensure_qkeee_env_file_skeleton", return_value=False)
    @patch.object(init_bot, "ensure_doctype", return_value=True)
    @patch.object(init_bot, "ensure_role", return_value=True)
    @patch.object(init_bot.core_client, "health_check", return_value={"status": "ok"})
    @patch.object(init_bot, "compute_plan", return_value={"role_needed": True, "doctypes_needed": ["Qkeee Bot Audit Log"]})
    def test_log_role_provisioning_called_after_doctype_loop(
            self, mocked_plan, mocked_health, mocked_role, mocked_doctype, mocked_env, mocked_log):
        calls = []
        mocked_doctype.side_effect = lambda *a, **k: calls.append("doctype") or True
        mocked_role.side_effect = lambda *a, **k: calls.append("role") or True
        mocked_log.side_effect = lambda *a, **k: calls.append("log")

        issued_at = int(time.time())
        token = init_bot._init_plan_token("qa", "admin@org.com", True,
                                           ["Qkeee Bot Audit Log"], issued_at=issued_at)
        init_bot.run_real("qa", "admin@org.com", confirm_token=token, issued_at=issued_at)

        self.assertEqual(calls, ["role", "doctype", "log"])
        mocked_log.assert_called_once_with("qa", "admin@org.com", True, unittest.mock.ANY)


if __name__ == "__main__":
    unittest.main()
