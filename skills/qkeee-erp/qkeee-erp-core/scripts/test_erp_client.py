#!/usr/bin/env python3
"""Regression tests for the audit-logging silent-failure bug: session_id
missing must never produce an empty `session` field on Qkeee Bot Audit Log
(that field is mandatory, and _audit_insert() swallows the resulting
MandatoryError silently — see bot-doctypes-design.md decision 10)."""

import unittest
import unittest.mock
from unittest.mock import patch

import erp_client
import erp_client as ec


class GetEnvConfigDebugRequestedByTests(unittest.TestCase):
    """QKEEE_ERP_<TAG>_DEBUG / _REQUESTED_BY are optional, per-tag, and
    must never block connection like BASE_URL/API_KEY/API_SECRET do."""

    ENV_BASE = {
        "QKEEE_ERP_DEFAULT_BASE_URL": "https://org.erpnext.com",
        "QKEEE_ERP_DEFAULT_API_KEY": "key",
        "QKEEE_ERP_DEFAULT_API_SECRET": "secret",
    }

    def test_defaults_when_absent(self):
        with patch.dict("os.environ", self.ENV_BASE, clear=True):
            cfg = ec.get_env_config("default")
        self.assertEqual(cfg["debug_default"], False)
        self.assertEqual(cfg["requested_by_default"], "")

    def test_resolves_per_tag_values(self):
        env = dict(self.ENV_BASE, QKEEE_ERP_DEFAULT_DEBUG="true",
                   QKEEE_ERP_DEFAULT_REQUESTED_BY="priya@org.com")
        with patch.dict("os.environ", env, clear=True):
            cfg = ec.get_env_config("default")
        self.assertEqual(cfg["debug_default"], True)
        self.assertEqual(cfg["requested_by_default"], "priya@org.com")

    def test_debug_env_truthy_variants(self):
        for raw, expected in (("1", True), ("true", True), ("True", True),
                               ("yes", True), ("on", True), ("0", False),
                               ("false", False), ("", False), ("nope", False)):
            env = dict(self.ENV_BASE, QKEEE_ERP_DEFAULT_DEBUG=raw)
            with patch.dict("os.environ", env, clear=True):
                cfg = ec.get_env_config("default")
            self.assertEqual(cfg["debug_default"], expected, f"raw={raw!r}")

    def test_different_tags_are_independent(self):
        env = dict(self.ENV_BASE,
                    QKEEE_ERP_QA_BASE_URL="https://qa.erpnext.com",
                    QKEEE_ERP_QA_API_KEY="qa-key",
                    QKEEE_ERP_QA_API_SECRET="qa-secret",
                    QKEEE_ERP_QA_DEBUG="true",
                    QKEEE_ERP_QA_REQUESTED_BY="qa-user@org.com")
        with patch.dict("os.environ", env, clear=True):
            default_cfg = ec.get_env_config("default")
            qa_cfg = ec.get_env_config("qa")
        self.assertEqual(default_cfg["debug_default"], False)
        self.assertEqual(default_cfg["requested_by_default"], "")
        self.assertEqual(qa_cfg["debug_default"], True)
        self.assertEqual(qa_cfg["requested_by_default"], "qa-user@org.com")


class SessionFallbackTests(unittest.TestCase):
    def test_session_or_fallback_passthrough(self):
        self.assertEqual(ec._session_or_fallback("sess-123"), "sess-123")

    def test_session_or_fallback_generates_nonempty_id(self):
        for missing in (None, ""):
            with self.subTest(missing=missing):
                result = ec._session_or_fallback(missing)
                self.assertTrue(result)
                self.assertTrue(result.startswith("local-"))

    @patch.object(ec, "_audit_insert")
    def test_log_read_never_sends_empty_session(self, mock_insert):
        ec._log_read(
            {"tag": "default"}, "Sales Order", "SO-0001",
            requested_by="user@example.com", session_id=None, persona_code=None,
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertTrue(sent_fields["session"])

    @patch.object(ec, "_audit_insert")
    def test_record_audit_log_start_never_sends_empty_session(self, mock_insert):
        ec.record_audit_log_start(
            {"tag": "default"}, action="Create", doctype="Sales Order", name=None,
            requested_by="user@example.com", session_id=None,
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertTrue(sent_fields["session"])


class AuditInsertFailureVisibilityTests(unittest.TestCase):
    @patch.object(ec, "_request", side_effect=RuntimeError("boom"))
    def test_audit_insert_warns_on_stderr_and_returns_none(self, mock_request):
        with patch("sys.stderr") as mock_stderr:
            result = ec._audit_insert({"tag": "default"}, {"session": "s1"})
        self.assertIsNone(result)
        self.assertTrue(mock_stderr.write.called)


class EnsurePersonaRegisteredTests(unittest.TestCase):
    """ensure_persona_registered() is unconditional master-data upsert —
    never a log, never gated on debug. Covers the three real outcomes: the
    persona row already exists (no-op), it doesn't and gets created, and the
    target instance hasn't provisioned Qkeee Bot Persona at all (swallowed
    failure, never raises)."""

    @patch.object(ec, "resource_exists", return_value=True)
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_request")
    def test_noop_when_already_registered(self, mock_request, mock_get_env_config, mock_resource_exists):
        result = ec.ensure_persona_registered(
            "default", persona_code="qkeee-erp-sales", persona_label="Sales",
        )
        mock_resource_exists.assert_called_once_with("default", ec.PERSONA_DOCTYPE, "qkeee-erp-sales")
        mock_request.assert_not_called()
        self.assertEqual(result, "already_registered")

    @patch.object(ec, "resource_exists", return_value=False)
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_request")
    def test_creates_when_not_registered(self, mock_request, mock_get_env_config, mock_resource_exists):
        result = ec.ensure_persona_registered(
            "default", persona_code="qkeee-erp-sales", persona_label="Sales",
            default_mode="read-write", non_negotiables="never bulk-delete",
        )
        mock_request.assert_called_once()
        self.assertEqual(result, "created")
        cfg_arg, method, path = mock_request.call_args[0][:3]
        self.assertEqual(cfg_arg, {"tag": "default"})
        self.assertEqual(method, "POST")
        self.assertIn(ec.PERSONA_DOCTYPE.replace(" ", "%20"), path)
        payload = mock_request.call_args[1]["payload"]
        self.assertEqual(payload["doctype"], ec.PERSONA_DOCTYPE)
        self.assertEqual(payload["persona_code"], "qkeee-erp-sales")
        self.assertEqual(payload["persona_label"], "Sales")
        self.assertEqual(payload["default_mode"], "Read Write")
        self.assertEqual(payload["non_negotiables"], "never bulk-delete")

    @patch.object(ec, "resource_exists", return_value=False)
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_request", side_effect=ec.ConnectorError("Persona doctype not provisioned"))
    def test_swallows_failure_when_doctype_not_provisioned(self, mock_request, mock_get_env_config, mock_resource_exists):
        with patch("sys.stderr") as mock_stderr:
            result = ec.ensure_persona_registered(
                "default", persona_code="qkeee-erp-sales", persona_label="Sales",
            )
        self.assertEqual(result, "failed")
        self.assertTrue(mock_stderr.write.called)


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


if __name__ == "__main__":
    unittest.main()
