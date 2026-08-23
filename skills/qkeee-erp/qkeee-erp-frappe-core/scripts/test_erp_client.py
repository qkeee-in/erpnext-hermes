#!/usr/bin/env python3
"""Regression tests for the audit-logging silent-failure bug: session_id
missing must never produce an empty `session` field on Qkeee Bot Audit Log
(that field is mandatory, and _audit_insert() swallows the resulting
MandatoryError silently — see bot-doctypes-design.md decision 10)."""

import time
import unittest
import unittest.mock
from unittest.mock import patch

import erp_client
import erp_client as ec
from confirm_token import advisory_write_token


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

    @patch.object(ec, "_audit_insert")
    @patch.object(ec, "resource_exists", return_value=False)
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_request")
    def test_creates_when_not_registered(self, mock_request, mock_get_env_config, mock_resource_exists,
                                          mock_audit_insert):
        mock_request.return_value = {"data": {"name": "qkeee-erp-sales"}}
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
        # PERSONA_DOCTYPE is no longer AUDIT_EXEMPT — the create is audited
        # via a single-shot _audit_insert() (not the two-phase write-path
        # helpers, which are excluded from read-only personas' own copy).
        mock_audit_insert.assert_called_once()
        audit_fields = mock_audit_insert.call_args[0][1]
        self.assertEqual(audit_fields["action"], "Create")
        self.assertEqual(audit_fields["reference_doctype"], ec.PERSONA_DOCTYPE)
        self.assertEqual(audit_fields["reference_name"], "qkeee-erp-sales")
        self.assertEqual(audit_fields["status"], "Success")

    @patch.object(ec, "_audit_insert")
    @patch.object(ec, "resource_exists", return_value=False)
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_request", side_effect=ec.ConnectorError("Persona doctype not provisioned"))
    def test_swallows_failure_when_doctype_not_provisioned(self, mock_request, mock_get_env_config,
                                                             mock_resource_exists, mock_audit_insert):
        with patch("sys.stderr") as mock_stderr:
            result = ec.ensure_persona_registered(
                "default", persona_code="qkeee-erp-sales", persona_label="Sales",
            )
        self.assertEqual(result, "failed")
        self.assertTrue(mock_stderr.write.called)
        mock_audit_insert.assert_called_once()
        audit_fields = mock_audit_insert.call_args[0][1]
        self.assertEqual(audit_fields["status"], "Failure")
        self.assertEqual(audit_fields["error_detail"], "Persona doctype not provisioned")


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


class TestGatedMutateResource(unittest.TestCase):
    """gated_mutate_resource() is this skill's own write entry point,
    merged in from the former qkeee-erp-catch-all skill (2026-08-18) —
    this skill's own extra layer on top of mutate_resource()'s
    mode/requested_by gate, enforced in code, not just prompt."""

    QA_ENV = {
        "QKEEE_ERP_QA_BASE_URL": "https://example.com",
        "QKEEE_ERP_QA_API_KEY": "key",
        "QKEEE_ERP_QA_API_SECRET": "secret",
    }

    def test_refuses_without_token(self):
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.ConnectorError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write",
                                          requested_by="priya@org.com")
            mocked_request.assert_not_called()

    def test_refuses_with_stale_token(self):
        old_issued_at = int(time.time()) - 10_000  # well past DEFAULT_TOKEN_TTL_SECONDS
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", old_issued_at)
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.StaleConfirmationError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=old_issued_at)
            mocked_request.assert_not_called()

    def test_refuses_with_mismatched_payload(self):
        """The rendered token is bound to the exact payload — a caller
        can't render one draft and execute a different one under the
        same token."""
        issued_at = int(time.time())
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", issued_at)
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.ConnectorError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 2}, mode="read-write",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=issued_at)
            mocked_request.assert_not_called()

    def test_succeeds_with_matching_fresh_token(self):
        issued_at = int(time.time())
        payload = {"lead_name": "Acme"}
        token = advisory_write_token("create", "CRM Lead", None, payload, "priya@org.com", issued_at)
        with patch.object(ec, "record_comment"), \
                patch.object(ec, "_audit_insert", return_value=None), \
                patch.object(ec, "_audit_update", return_value=False), \
                patch.object(ec, "_audit_submit", return_value=False), \
                patch.dict("os.environ", self.QA_ENV, clear=True), \
                patch.object(ec, "_request", return_value={"data": {"name": "CRM-LEAD-0001"}}) as mocked:
            result = ec.gated_mutate_resource("qa", "CRM Lead", "create", payload, mode="read-write",
                                               requested_by="priya@org.com",
                                               confirmation_token=token, issued_at=issued_at)
        self.assertEqual(result["data"]["name"], "CRM-LEAD-0001")
        mocked.assert_called_once()

    def test_still_refuses_read_only_even_with_valid_token(self):
        """The token gate is additive, not a replacement for the
        mode/requested_by gate mutate_resource() already enforces."""
        issued_at = int(time.time())
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", issued_at)
        with patch.object(ec, "_request") as mocked_request:
            with self.assertRaises(ec.ReadOnlyModeError):
                ec.gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-only",
                                          requested_by="priya@org.com",
                                          confirmation_token=token, issued_at=issued_at)
            mocked_request.assert_not_called()


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
