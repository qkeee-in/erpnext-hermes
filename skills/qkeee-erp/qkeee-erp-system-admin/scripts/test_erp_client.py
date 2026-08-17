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
            mock_req.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()
