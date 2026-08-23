#!/usr/bin/env python3
import os
import time
import unittest
from unittest import mock

import erp_client
from confirm_token import (
    config_change_token,
    destructive_action_token,
    elevated_user_token,
    permission_change_token,
)


class TestEnvConfig(unittest.TestCase):
    def setUp(self):
        for k in list(os.environ):
            if k.startswith("QKEEE_ERP_"):
                del os.environ[k]

    def test_missing_vars_named_specifically(self):
        with self.assertRaises(erp_client.ConnectorError) as ctx:
            erp_client.get_env_config("qa")
        msg = str(ctx.exception)
        self.assertIn("QKEEE_ERP_QA_BASE_URL", msg)
        self.assertIn("QKEEE_ERP_QA_API_KEY", msg)
        self.assertIn("QKEEE_ERP_QA_API_SECRET", msg)

    def test_resolves_when_present(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://org-qa.erpnext.com/"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"
        cfg = erp_client.get_env_config("qa")
        self.assertEqual(cfg["base_url"], "https://org-qa.erpnext.com")
        self.assertEqual(cfg["api_key"], "k")

    def test_list_configured_tags_requires_full_set(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_PROD_BASE_URL"] = "https://y"
        os.environ["QKEEE_ERP_PROD_API_KEY"] = "k2"
        os.environ["QKEEE_ERP_PROD_API_SECRET"] = "s2"
        tags = erp_client.list_configured_tags()
        self.assertEqual(tags, ["PROD"])

    def test_refuses_non_https_base_url(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "http://org-qa.erpnext.com"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"
        with self.assertRaises(erp_client.ConnectorError) as ctx:
            erp_client.get_env_config("qa")
        self.assertIn("https", str(ctx.exception))

    def test_allows_non_https_with_explicit_override(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "http://localhost:8000"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"
        os.environ["QKEEE_ERP_QA_ALLOW_INSECURE"] = "1"
        cfg = erp_client.get_env_config("qa")
        self.assertEqual(cfg["base_url"], "http://localhost:8000")


class TestModeGate(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    def test_refuses_write_in_read_only(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.mutate_resource("qa", "User", "create", payload={}, mode="read-only")

    def test_refuses_write_with_default_mode(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.mutate_resource("qa", "User", "create", payload={})

    @mock.patch("erp_client._request")
    def test_allows_write_in_read_write(self, mock_req):
        mock_req.return_value = {"data": {"name": "test@example.com"}}
        result = erp_client.mutate_resource(
            "qa", "User", "create", payload={"email": "test@example.com"}, mode="read-write",
            requested_by="priya@org.com",
        )
        self.assertEqual(result["data"]["name"], "test@example.com")

    def test_update_requires_name(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.mutate_resource("qa", "User", "update", payload={}, mode="read-write",
                                        requested_by="priya@org.com")

    def test_refuses_write_without_requested_by(self):
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.mutate_resource("qa", "User", "create", payload={}, mode="read-write")


class TestQueryResource(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    @mock.patch("erp_client._request")
    def test_has_more_flag_when_truncated(self, mock_req):
        mock_req.return_value = {"data": [{"name": f"user-{i}"} for i in range(21)]}
        result = erp_client.query_resource("qa", "User", limit=20)
        self.assertEqual(len(result["data"]), 20)
        self.assertTrue(result["has_more"])

    @mock.patch("erp_client._request")
    def test_no_has_more_when_exact(self, mock_req):
        mock_req.return_value = {"data": [{"name": f"user-{i}"} for i in range(5)]}
        result = erp_client.query_resource("qa", "User", limit=20)
        self.assertEqual(len(result["data"]), 5)
        self.assertFalse(result["has_more"])


class TestDestructiveMutate(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"
        self.now = int(time.time())

    def test_refuses_in_read_only(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.destructive_mutate(
                "qa", "User", "delete", "test@example.com", "cleanup",
                mode="read-only", confirmation_token="whatever", issued_at=self.now,
            )

    def test_requires_reason(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.destructive_mutate(
                "qa", "User", "delete", "test@example.com", "",
                mode="read-write", confirmation_token="whatever", issued_at=self.now,
            )

    def test_requires_token(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.destructive_mutate(
                "qa", "User", "delete", "test@example.com", "cleanup",
                mode="read-write", confirmation_token=None, issued_at=self.now,
                requested_by="priya@org.com",
            )

    def test_requires_issued_at(self):
        token = destructive_action_token("delete_user", "User", "test@example.com", "cleanup", self.now)
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.destructive_mutate(
                "qa", "User", "delete", "test@example.com", "cleanup",
                mode="read-write", confirmation_token=token, issued_at=None,
                requested_by="priya@org.com",
            )

    def test_rejects_mismatched_token(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.destructive_mutate(
                "qa", "User", "delete", "test@example.com", "cleanup",
                mode="read-write", confirmation_token="not-the-real-token", issued_at=self.now,
                requested_by="priya@org.com",
            )

    def test_rejects_stale_token(self):
        stale_issued_at = self.now - 3600  # 1 hour old, well past the 15-minute TTL
        token = destructive_action_token("delete_user", "User", "test@example.com", "cleanup", stale_issued_at)
        with self.assertRaises(erp_client.StaleConfirmationError):
            erp_client.destructive_mutate(
                "qa", "User", "delete", "test@example.com", "cleanup",
                mode="read-write", confirmation_token=token, issued_at=stale_issued_at,
                requested_by="priya@org.com",
            )

    def test_rejects_future_issued_at(self):
        future_issued_at = self.now + 3600
        token = destructive_action_token("delete_user", "User", "test@example.com", "cleanup", future_issued_at)
        with self.assertRaises(erp_client.StaleConfirmationError):
            erp_client.destructive_mutate(
                "qa", "User", "delete", "test@example.com", "cleanup",
                mode="read-write", confirmation_token=token, issued_at=future_issued_at,
                requested_by="priya@org.com",
            )

    def test_refuses_without_requested_by(self):
        token = destructive_action_token("delete_user", "User", "test@example.com", "cleanup", self.now)
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.destructive_mutate(
                "qa", "User", "delete", "test@example.com", "cleanup",
                mode="read-write", confirmation_token=token, issued_at=self.now,
            )

    def test_rejects_unsupported_action(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.destructive_mutate(
                "qa", "User", "submit", "test@example.com", "cleanup",
                mode="read-write", confirmation_token="whatever", issued_at=self.now,
            )

    def test_rejects_update_on_non_user_doctype(self):
        # "update" is only defined for User (disable); anything else must
        # come through as "delete" — otherwise action_key derivation would
        # silently mismatch what the render step computed.
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.destructive_mutate(
                "qa", "Webhook", "update", "WH-001", "cleanup",
                mode="read-write", confirmation_token="whatever", issued_at=self.now,
            )

    @mock.patch("erp_client._audit_insert", return_value=None)
    @mock.patch("erp_client._audit_update", return_value=False)
    @mock.patch("erp_client._audit_submit", return_value=False)
    @mock.patch("erp_client._request")
    def test_accepts_matching_token_for_delete(self, mock_req, mock_audit_submit,
                                                mock_audit_update, mock_audit_insert):
        mock_req.return_value = {"data": "ok"}
        token = destructive_action_token("delete_user", "User", "test@example.com", "cleanup", self.now)
        result = erp_client.destructive_mutate(
            "qa", "User", "delete", "test@example.com", "cleanup",
            mode="read-write", confirmation_token=token, issued_at=self.now,
            requested_by="priya@org.com",
        )
        self.assertEqual(result["data"], "ok")
        # one call for the best-effort audit comment, one for the delete itself
        self.assertEqual(mock_req.call_count, 2)
        comment_call = mock_req.call_args_list[0]
        self.assertEqual(comment_call.args[2], "/api/method/frappe.desk.form.utils.add_comment")
        self.assertIn("priya@org.com", comment_call.kwargs["payload"]["content"])
        self.assertIn("cleanup", comment_call.kwargs["payload"]["content"])

    @mock.patch("erp_client._request")
    def test_delete_proceeds_even_if_audit_comment_fails(self, mock_req):
        def side_effect(cfg, method, path, params=None, payload=None):
            if "add_comment" in path:
                raise erp_client.ConnectorError("no permission to comment")
            return {"data": "ok"}
        mock_req.side_effect = side_effect
        token = destructive_action_token("delete_user", "User", "test@example.com", "cleanup", self.now)
        result = erp_client.destructive_mutate(
            "qa", "User", "delete", "test@example.com", "cleanup",
            mode="read-write", confirmation_token=token, issued_at=self.now,
            requested_by="priya@org.com",
        )
        self.assertEqual(result["data"], "ok")

    @mock.patch("erp_client._request")
    def test_accepts_matching_token_for_disable(self, mock_req):
        mock_req.return_value = {"data": {"enabled": 0}}
        token = destructive_action_token("disable_user", "User", "test@example.com", "left the org", self.now)
        result = erp_client.destructive_mutate(
            "qa", "User", "update", "test@example.com", "left the org",
            mode="read-write", confirmation_token=token, issued_at=self.now, payload={"enabled": 0},
            requested_by="priya@org.com",
        )
        self.assertEqual(result["data"]["enabled"], 0)


class TestCallPermissionManager(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"
        self.now = int(time.time())

    def test_refuses_in_read_only(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.call_permission_manager(
                "qa", "add", "Contact", "Auditor", 0, mode="read-only",
            )

    def test_add_requires_role(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.call_permission_manager(
                "qa", "add", "Contact", "", 0, mode="read-write",
                confirmation_token="whatever", issued_at=self.now,
            )

    def test_reset_does_not_require_role(self):
        token = permission_change_token("reset", "Contact", "", 0, "", None, self.now)
        with mock.patch("erp_client._request") as mock_req:
            mock_req.return_value = {}
            erp_client.call_permission_manager(
                "qa", "reset", "Contact", "", 0, mode="read-write",
                confirmation_token=token, issued_at=self.now,
                requested_by="priya@org.com",
            )
            # Two calls: the Attempted audit-log insert, then the actual
            # reset POST (record_audit_log_finish() no-ops here since the
            # mocked {} response makes _audit_insert() resolve no log name).
            self.assertEqual(mock_req.call_count, 2)
            reset_call = mock_req.call_args_list[-1]
            self.assertEqual(
                reset_call.args[2],
                "/api/method/frappe.core.page.permission_manager.permission_manager.reset",
            )
            self.assertEqual(reset_call.kwargs["payload"], {"doctype": "Contact"})

    def test_update_requires_matching_token(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.call_permission_manager(
                "qa", "update", "Contact", "Auditor", 0, ptype="write", value=1,
                mode="read-write", confirmation_token="stale-token", issued_at=self.now,
                requested_by="priya@org.com",
            )

    def test_update_rejects_stale_issued_at(self):
        stale = self.now - 3600
        token = permission_change_token("update", "Contact", "Auditor", 0, "write", 1, stale)
        with self.assertRaises(erp_client.StaleConfirmationError):
            erp_client.call_permission_manager(
                "qa", "update", "Contact", "Auditor", 0, ptype="write", value=1,
                mode="read-write", confirmation_token=token, issued_at=stale,
                requested_by="priya@org.com",
            )

    def test_update_accepts_matching_token(self):
        token = permission_change_token("update", "Contact", "Auditor", 0, "write", 1, self.now)
        with mock.patch("erp_client._request") as mock_req:
            mock_req.return_value = {}
            erp_client.call_permission_manager(
                "qa", "update", "Contact", "Auditor", 0, ptype="write", value=1,
                mode="read-write", confirmation_token=token, issued_at=self.now,
                requested_by="priya@org.com",
            )
            sent = mock_req.call_args.kwargs.get("payload") or mock_req.call_args[0][3]
            self.assertEqual(sent, {"doctype": "Contact", "role": "Auditor", "permlevel": 0,
                                     "ptype": "write", "value": 1, "if_owner": 0})

    def test_refuses_without_requested_by(self):
        token = permission_change_token("update", "Contact", "Auditor", 0, "write", 1, self.now)
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.call_permission_manager(
                "qa", "update", "Contact", "Auditor", 0, ptype="write", value=1,
                mode="read-write", confirmation_token=token, issued_at=self.now,
            )

    @mock.patch("erp_client._request")
    def test_get_permissions_never_token_gated(self, mock_req):
        mock_req.return_value = {"message": [{"role": "Auditor"}]}
        result = erp_client.get_permissions("qa", "Contact")
        self.assertEqual(result, [{"role": "Auditor"}])

    def test_success_logs_attempted_then_success_to_audit_log(self):
        """Regression test for the 2026-08-23 audit-logging fix:
        call_permission_manager() used to be the one write path in the
        library that never reached Qkeee Bot Audit Log at all. Assert
        both phases actually fire, with the doctype/synthetic-name shape
        documented in the function's own docstring."""
        token = permission_change_token("update", "Contact", "Auditor", 0, "write", 1, self.now)
        with mock.patch("erp_client._audit_insert") as mock_insert, \
                mock.patch("erp_client._audit_update") as mock_update, \
                mock.patch("erp_client._audit_submit") as mock_submit, \
                mock.patch("erp_client._request") as mock_req:
            mock_insert.return_value = "AUDIT-0001"
            mock_update.return_value = True
            mock_req.return_value = {}
            erp_client.call_permission_manager(
                "qa", "update", "Contact", "Auditor", 0, ptype="write", value=1,
                mode="read-write", confirmation_token=token, issued_at=self.now,
                requested_by="priya@org.com",
            )
            start_fields = mock_insert.call_args[0][1]
            self.assertEqual(start_fields["status"], "Attempted")
            self.assertEqual(start_fields["action"], "Permission Update")
            self.assertEqual(start_fields["reference_doctype"], "Contact")
            self.assertEqual(start_fields["reference_name"], "Auditor@permlevel0")
            self.assertEqual(start_fields["requested_by"], "priya@org.com")
            self.assertEqual(start_fields["user_approved"], "Approved")

            finish_fields = mock_update.call_args[0][2]
            self.assertEqual(finish_fields["status"], "Success")
            mock_submit.assert_called_once_with(mock.ANY, "AUDIT-0001")

    def test_failure_logs_attempted_then_failure_to_audit_log(self):
        """The RPC call itself failing must still flip the Attempted row
        to Failure (and re-raise) — same contract as mutate_resource()."""
        token = permission_change_token("update", "Contact", "Auditor", 0, "write", 1, self.now)
        with mock.patch("erp_client._audit_insert") as mock_insert, \
                mock.patch("erp_client._audit_update") as mock_update, \
                mock.patch("erp_client._request") as mock_req:
            mock_insert.return_value = "AUDIT-0002"
            mock_req.side_effect = erp_client.ConnectorError("boom")
            with self.assertRaises(erp_client.ConnectorError):
                erp_client.call_permission_manager(
                    "qa", "update", "Contact", "Auditor", 0, ptype="write", value=1,
                    mode="read-write", confirmation_token=token, issued_at=self.now,
                    requested_by="priya@org.com",
                )
            finish_fields = mock_update.call_args[0][2]
            self.assertEqual(finish_fields["status"], "Failure")


class TestCreateUser(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"
        self.now = int(time.time())

    @mock.patch("erp_client._request")
    def test_non_elevated_role_needs_no_token(self, mock_req):
        mock_req.return_value = {"data": {"name": "a@b.com"}}
        result = erp_client.create_user("qa", "a@b.com", "A", ["Auditor"], mode="read-write",
                                         requested_by="priya@org.com")
        self.assertEqual(result["data"]["name"], "a@b.com")

    def test_refuses_without_requested_by(self):
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.create_user("qa", "a@b.com", "A", ["Auditor"], mode="read-write")

    def test_elevated_role_requires_token(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.create_user("qa", "a@b.com", "A", ["System Manager"], mode="read-write")

    def test_elevated_role_rejects_mismatched_token(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.create_user(
                "qa", "a@b.com", "A", ["System Manager"], mode="read-write",
                elevated_confirmation_token="bogus", issued_at=self.now,
            )

    def test_elevated_role_rejects_stale_token(self):
        stale = self.now - 3600
        token = elevated_user_token("a@b.com", ["System Manager"], stale)
        with self.assertRaises(erp_client.StaleConfirmationError):
            erp_client.create_user(
                "qa", "a@b.com", "A", ["System Manager"], mode="read-write",
                elevated_confirmation_token=token, issued_at=stale,
            )

    @mock.patch("erp_client._request")
    def test_elevated_role_accepts_matching_fresh_token(self, mock_req):
        mock_req.return_value = {"data": {"name": "a@b.com"}}
        token = elevated_user_token("a@b.com", ["System Manager"], self.now)
        result = erp_client.create_user(
            "qa", "a@b.com", "A", ["System Manager"], mode="read-write",
            elevated_confirmation_token=token, issued_at=self.now,
            requested_by="priya@org.com",
        )
        self.assertEqual(result["data"]["name"], "a@b.com")

    def test_refuses_in_read_only(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.create_user("qa", "a@b.com", "A", ["Auditor"], mode="read-only")


class TestGatedConfigMutate(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"
        self.now = int(time.time())

    def test_refuses_in_read_only(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.gated_config_mutate(
                "qa", "create_webhook", "Webhook", "https://example.com/hook", "sync data",
                "create", mode="read-only", confirmation_token="x", issued_at=self.now,
            )

    def test_requires_reason(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.gated_config_mutate(
                "qa", "create_webhook", "Webhook", "https://example.com/hook", "",
                "create", mode="read-write", confirmation_token="x", issued_at=self.now,
            )

    def test_rejects_unknown_kind(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.gated_config_mutate(
                "qa", "bogus_kind", "Webhook", "https://example.com/hook", "reason",
                "create", mode="read-write", confirmation_token="x", issued_at=self.now,
            )

    def test_rejects_mismatched_token(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.gated_config_mutate(
                "qa", "create_webhook", "Webhook", "https://example.com/hook", "sync data",
                "create", mode="read-write", confirmation_token="wrong", issued_at=self.now,
            )

    @mock.patch("erp_client._request")
    def test_accepts_matching_fresh_token_for_webhook(self, mock_req):
        mock_req.return_value = {"data": {"name": "WH-001"}}
        token = config_change_token("create_webhook", "Webhook", "https://example.com/hook", "sync data", self.now)
        result = erp_client.gated_config_mutate(
            "qa", "create_webhook", "Webhook", "https://example.com/hook", "sync data",
            "create", payload={"request_url": "https://example.com/hook"},
            mode="read-write", confirmation_token=token, issued_at=self.now,
            requested_by="priya@org.com",
        )
        self.assertEqual(result["data"]["name"], "WH-001")

    @mock.patch("erp_client._request")
    def test_accepts_matching_fresh_token_for_workflow_toggle(self, mock_req):
        mock_req.return_value = {"data": {"is_active": 0}}
        token = config_change_token("toggle_workflow", "Workflow", "Leave Application", "pause approvals", self.now)
        result = erp_client.gated_config_mutate(
            "qa", "toggle_workflow", "Workflow", "Leave Application", "pause approvals",
            "update", name="Leave Approval Workflow", payload={"is_active": 0},
            mode="read-write", confirmation_token=token, issued_at=self.now,
            requested_by="priya@org.com",
        )
        self.assertEqual(result["data"]["is_active"], 0)

    def test_refuses_without_requested_by(self):
        token = config_change_token("create_webhook", "Webhook", "https://example.com/hook", "sync data", self.now)
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.gated_config_mutate(
                "qa", "create_webhook", "Webhook", "https://example.com/hook", "sync data",
                "create", payload={"request_url": "https://example.com/hook"},
                mode="read-write", confirmation_token=token, issued_at=self.now,
            )



import erp_client
import unittest.mock


class RunQueryReportTests(unittest.TestCase):
    """Fully-qualified `import erp_client` + `unittest.mock.patch.object`
    throughout — deliberately not relying on any particular alias
    (`ec`/`patch`) a specific copy of this test file might import under,
    so this class stays portable when appended into a persona skill's own
    test_erp_client.py by the core sync script."""

    @unittest.mock.patch.object(erp_client, "_log_read")
    @unittest.mock.patch.object(erp_client, "_request")
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_uses_get_and_wraps_message(self, mock_get_env_config, mock_request, mock_log_read):
        mock_request.return_value = {
            "message": {"columns": [{"fieldname": "customer"}], "result": [{"customer": "Acme"}]}
        }
        result = erp_client.run_query_report("default", "Sales Order Analysis", {"company": "Acme"})
        method, path = mock_request.call_args[0][1:3]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/api/method/frappe.desk.query_report.run")
        self.assertEqual(mock_request.call_args[1]["params"]["report_name"], "Sales Order Analysis")
        self.assertEqual(result["report_name"], "Sales Order Analysis")
        self.assertEqual(result["result"], [{"customer": "Acme"}])
        mock_log_read.assert_not_called()

    @unittest.mock.patch.object(erp_client, "_log_read")
    @unittest.mock.patch.object(erp_client, "_request", return_value={"message": {}})
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_debug_true_logs_against_report_doctype(self, mock_get_env_config, mock_request, mock_log_read):
        erp_client.run_query_report("default", "Trial Balance", debug=True, requested_by="user@example.com")
        mock_log_read.assert_called_once()
        self.assertEqual(mock_log_read.call_args[0][1], "Report")
        self.assertEqual(mock_log_read.call_args[0][2], "Trial Balance")


class GetUserRolesTests(unittest.TestCase):
    @unittest.mock.patch.object(erp_client, "_request")
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_resolves_given_user_roles(self, mock_get_env_config, mock_request):
        mock_request.return_value = {"data": {"roles": [{"role": "Purchase Manager"}, {"role": "Employee"}]}}
        result = erp_client.get_user_roles("default", "priya@org.com")
        self.assertEqual(result["user"], "priya@org.com")
        self.assertEqual(result["roles"], ["Purchase Manager", "Employee"])
        self.assertEqual(result["warning"], "")

    @unittest.mock.patch.object(erp_client, "_request")
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_self_resolves_when_no_user_given(self, mock_get_env_config, mock_request):
        mock_request.side_effect = [
            {"message": "bot@org.com"},
            {"data": {"roles": [{"role": "System Manager"}]}},
        ]
        result = erp_client.get_user_roles("default")
        self.assertEqual(result["user"], "bot@org.com")
        self.assertEqual(mock_request.call_args_list[0][0][2], "/api/method/frappe.auth.get_logged_user")

    @unittest.mock.patch.object(erp_client, "_request", return_value={"data": {"roles": []}})
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_empty_roles_surfaces_ambiguity_warning(self, mock_get_env_config, mock_request):
        result = erp_client.get_user_roles("default", "priya@org.com")
        self.assertEqual(result["roles"], [])
        self.assertTrue(result["warning"])
        self.assertIn("not confirmed", result["warning"])

class QkeeeEnvFileTests(unittest.TestCase):
    """qkeee-erp.env is the isolated, execute_code-sandbox-safe credential
    source (see get_env_config()'s _qkeee_env() call) — these exercise the
    file parser and its precedence over os.environ directly, without
    touching the real filesystem location HERMES_HOME would resolve to.
    Fully-qualified `erp_client.*`/`unittest.mock.*` and a locally-imported
    `os` throughout, deliberately not relying on any particular alias this
    file's own top-of-file imports happen to use, so this class stays
    portable when appended into a persona skill's own test_erp_client.py."""

    def setUp(self):
        import os
        erp_client._QKEEE_ENV_FILE_CACHE = None
        self.addCleanup(setattr, erp_client, "_QKEEE_ENV_FILE_CACHE", None)

    def _with_file(self, contents):
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(contents)
        self.addCleanup(os.remove, path)
        return path

    def test_file_values_take_precedence_over_os_environ(self):
        import os
        path = self._with_file(
            "QKEEE_ERP_QA_BASE_URL=https://from-file.example.com\n"
        )
        env = {
            "QKEEE_ERP_QA_BASE_URL": "https://from-environ.example.com",
            "QKEEE_ERP_QA_API_KEY": "key",
            "QKEEE_ERP_QA_API_SECRET": "secret",
        }
        with unittest.mock.patch.object(erp_client, "_qkeee_env_file_path", return_value=path), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            cfg = erp_client.get_env_config("qa")
        self.assertEqual(cfg["base_url"], "https://from-file.example.com")

    def test_missing_file_falls_back_to_os_environ(self):
        import os
        env = {
            "QKEEE_ERP_QA_BASE_URL": "https://example.com",
            "QKEEE_ERP_QA_API_KEY": "key",
            "QKEEE_ERP_QA_API_SECRET": "secret",
        }
        with unittest.mock.patch.object(erp_client, "_qkeee_env_file_path", return_value="/nonexistent/qkeee-erp.env"), \
                unittest.mock.patch.dict(os.environ, env, clear=True):
            cfg = erp_client.get_env_config("qa")
        self.assertEqual(cfg["base_url"], "https://example.com")

    def test_comments_blank_lines_and_quoted_values(self):
        import os
        path = self._with_file(
            "# a comment\n"
            "\n"
            "QKEEE_ERP_QA_BASE_URL=\"https://quoted.example.com\"\n"
            "QKEEE_ERP_QA_API_KEY=key\n"
            "QKEEE_ERP_QA_API_SECRET='secret'\n"
        )
        with unittest.mock.patch.object(erp_client, "_qkeee_env_file_path", return_value=path), \
                unittest.mock.patch.dict(os.environ, {}, clear=True):
            cfg = erp_client.get_env_config("qa")
        self.assertEqual(cfg["base_url"], "https://quoted.example.com")
        self.assertEqual(cfg["api_key"], "key")
        self.assertEqual(cfg["api_secret"], "secret")


if __name__ == "__main__":
    unittest.main()
