#!/usr/bin/env python3
"""Regression tests for core.client. Imports the module under both the
`erp_client` and `ec` aliases.

Regression tests for the audit-logging silent-failure bug: session_id
missing must never produce an empty `session` field on Qkeee Bot Audit Log
(that field is mandatory, and _audit_insert() swallows the resulting
MandatoryError silently)."""

import time
import unittest
import unittest.mock
from unittest.mock import patch

import client as erp_client
import client as ec
from confirm_token import advisory_write_token


class GetEnvConfigRequestedByTests(unittest.TestCase):
    """QKEEE_ERP_<TAG>_REQUESTED_BY is optional, per-tag, and must never
    block connection like BASE_URL/API_KEY/API_SECRET do.

    There is no QKEEE_ERP_<TAG>_DEBUG / `debug_default`: read audit
    logging is always on, so there is no debug flag to resolve."""

    ENV_BASE = {
        "QKEEE_ERP_DEFAULT_BASE_URL": "https://org.erpnext.com",
        "QKEEE_ERP_DEFAULT_API_KEY": "key",
        "QKEEE_ERP_DEFAULT_API_SECRET": "secret",
    }

    def test_defaults_when_absent(self):
        with patch.dict("os.environ", self.ENV_BASE, clear=True):
            cfg = ec.get_env_config("default")
        self.assertEqual(cfg["requested_by_default"], "")
        self.assertNotIn("debug_default", cfg)

    def test_resolves_per_tag_values(self):
        env = dict(self.ENV_BASE, QKEEE_ERP_DEFAULT_REQUESTED_BY="priya@org.com")
        with patch.dict("os.environ", env, clear=True):
            cfg = ec.get_env_config("default")
        self.assertEqual(cfg["requested_by_default"], "priya@org.com")

    def test_different_tags_are_independent(self):
        env = dict(self.ENV_BASE,
                    QKEEE_ERP_QA_BASE_URL="https://qa.erpnext.com",
                    QKEEE_ERP_QA_API_KEY="qa-key",
                    QKEEE_ERP_QA_API_SECRET="qa-secret",
                    QKEEE_ERP_QA_REQUESTED_BY="qa-user@org.com")
        with patch.dict("os.environ", env, clear=True):
            default_cfg = ec.get_env_config("default")
            qa_cfg = ec.get_env_config("qa")
        self.assertEqual(default_cfg["requested_by_default"], "")
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
            requested_by="user@example.com", session_id=None, domain_code=None,
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


class AuditSubmitSkipsWhenInsertFailedTests(unittest.TestCase):
    """_audit_submit(None) — reached whenever the preceding insert already
    failed and warned — must not raise a second, confusing warning
    (urllib.parse.quote(None) -> "quote_from_bytes() expected bytes") that
    would mask the real cause. Guard: log_name falsy short-circuits to
    False before any request."""

    def test_returns_false_without_a_request_when_log_name_is_none(self):
        with patch.object(ec, "_request") as mocked_request:
            result = ec._audit_submit({"tag": "default"}, None)
        self.assertFalse(result)
        mocked_request.assert_not_called()


class PersonaDoctypeRemovedTests(unittest.TestCase):
    """There is no `Qkeee Bot Persona` doctype — `ensure_persona_registered()`,
    `PERSONA_DOCTYPE`, and the `register-persona` CLI subcommand don't
    exist in core/client.py. These are guard tests, not coverage of a
    capability: they fail loudly if a future edit reintroduces
    persona-registration code without a deliberate decision to do so (see
    ../../CHANGELOG.md for the exported schema/manifest this replaces)."""

    def test_ensure_persona_registered_does_not_exist(self):
        self.assertFalse(hasattr(ec, "ensure_persona_registered"))

    def test_persona_doctype_constant_does_not_exist(self):
        self.assertFalse(hasattr(ec, "PERSONA_DOCTYPE"))

    def test_register_persona_not_a_cli_subcommand(self):
        # argparse raises SystemExit(2) for an unrecognized subcommand —
        # confirm "register-persona" is one, not just that it errors for
        # some other reason (e.g. a missing required flag).
        with patch("sys.argv", ["client.py", "--tag", "default", "register-persona",
                                 "--domain-code", "x"]), \
                patch("sys.stderr", new_callable=__import__("io").StringIO) as mock_stderr:
            with self.assertRaises(SystemExit):
                ec._cli()
        self.assertIn("invalid choice", mock_stderr.getvalue())
        self.assertIn("register-persona", mock_stderr.getvalue())


class AuditLogDomainCodeTests(unittest.TestCase):
    """Qkeee Bot Audit Log's `domain_code` field — a denormalized string
    naming the active qkeee-erp-associate domain reference (e.g.
    'qkeee-erp-associate/hr-payroll'). Confirms the write payload carries
    `domain_code`, not `persona_code`."""

    @patch.object(ec, "_audit_insert")
    def test_log_read_writes_domain_code_field(self, mock_insert):
        ec._log_read(
            {"tag": "default"}, "Sales Order", "SO-0001",
            requested_by="user@example.com", session_id="s1",
            domain_code="qkeee-erp-associate/sales",
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertEqual(sent_fields["domain_code"], "qkeee-erp-associate/sales")
        self.assertNotIn("persona_code", sent_fields)

    @patch.object(ec, "_audit_insert")
    def test_record_audit_log_start_writes_domain_code_field(self, mock_insert):
        ec.record_audit_log_start(
            {"tag": "default"}, action="Create", doctype="Sales Order", name=None,
            requested_by="user@example.com", session_id="s1",
            domain_code="qkeee-erp-associate/sales",
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertEqual(sent_fields["domain_code"], "qkeee-erp-associate/sales")
        self.assertNotIn("persona_code", sent_fields)


class RunQueryReportTests(unittest.TestCase):
    """Fully-qualified `import erp_client` + `unittest.mock.patch.object`
    throughout, deliberately not relying on the `ec`/`patch` aliases used
    elsewhere in this file."""

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
        # Read logging is unconditional, not debug-gated — every read logs.
        mock_log_read.assert_called_once()

    @unittest.mock.patch.object(erp_client, "check_user_permission", return_value=True)
    @unittest.mock.patch.object(erp_client, "resource_exists", return_value=True)
    @unittest.mock.patch.object(erp_client, "_log_read")
    @unittest.mock.patch.object(erp_client, "_request", return_value={"message": {}})
    @unittest.mock.patch.object(erp_client, "get_env_config", return_value={"tag": "default"})
    def test_read_logs_against_report_doctype(self, mock_get_env_config, mock_request, mock_log_read,
                                               mock_resource_exists, mock_check_perm):
        erp_client.run_query_report("default", "Trial Balance", requested_by="user@example.com")
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
        # The requester-permission check runs on every tag, not PROD only
        # — a write on non-PROD 'qa' with a requested_by present still gets
        # validated as a real User with has_permission, so those two
        # need mocking here even though 'qa' isn't PROD.
        issued_at = int(time.time())
        payload = {"lead_name": "Acme"}
        token = advisory_write_token("create", "CRM Lead", None, payload, "priya@org.com", issued_at)
        with patch.object(ec, "record_comment"), \
                patch.object(ec, "_audit_insert", return_value=None), \
                patch.object(ec, "_audit_update", return_value=False), \
                patch.object(ec, "_audit_submit", return_value=False), \
                patch.object(ec, "resource_exists", return_value=True), \
                patch.object(ec, "check_user_permission", return_value=True), \
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
    `os` throughout, deliberately not relying on the aliases used
    elsewhere in this file."""

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


class IsProdTagTests(unittest.TestCase):
    def test_matches_various_casings_and_positions(self):
        for tag in ("prod", "PROD", "PROD_ERP", "client-a-prod", "Production"):
            self.assertTrue(ec._is_prod_tag(tag), tag)

    def test_non_prod_tags_dont_match(self):
        for tag in ("qa", "default", "staging", "dev", "hrms-demo"):
            self.assertFalse(ec._is_prod_tag(tag), tag)


class ValidateProdRequesterTests(unittest.TestCase):
    """_validate_prod_requester(): presence of requested_by stays
    mandatory on PROD only (no-op with a missing requester off PROD/on
    exempt doctypes). But whenever a requested_by IS present it is
    validated — as a real ERPNext User, with actual has_permission on the
    doctype/action — on EVERY tag, not PROD only. Never falls back to a
    tag default, never proceeds unverified. See
    UniversalRequesterValidationTests below for the non-PROD-with-a-requester
    cases."""

    def test_noop_on_non_prod_tag(self):
        with patch.object(ec, "resource_exists") as mocked_exists:
            ec._validate_prod_requester("qa", None, "Sales Order", "read")
        mocked_exists.assert_not_called()

    def test_noop_on_exempt_doctype_even_on_prod(self):
        with patch.object(ec, "resource_exists") as mocked_exists:
            ec._validate_prod_requester("prod", None, "User", "read")
        mocked_exists.assert_not_called()

    def test_refuses_missing_requester_on_prod(self):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("prod", None, "Sales Order", "read")
        self.assertIn("no requester was given", str(ctx.exception))

    def test_refuses_missing_requester_even_if_empty_string(self):
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("prod", "", "Sales Order", "read")

    @patch.object(ec, "resource_exists", return_value=False)
    def test_refuses_unknown_user(self, mocked_exists):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("prod", "nobody@org.com", "Sales Order", "read")
        mocked_exists.assert_called_once_with("prod", "User", "nobody@org.com")
        self.assertIn("not a known ERPNext User", str(ctx.exception))

    @patch.object(ec, "check_user_permission", return_value=False)
    @patch.object(ec, "resource_exists", return_value=True)
    def test_refuses_when_permission_check_returns_false(self, mocked_exists, mocked_perm):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("prod", "priya@org.com", "Sales Order", "write", docname="SO-0001")
        mocked_perm.assert_called_once_with("prod", "Sales Order", "write", "priya@org.com", "SO-0001")
        self.assertIn("does not have 'write' permission", str(ctx.exception))

    @patch.object(ec, "check_user_permission", return_value=True)
    @patch.object(ec, "resource_exists", return_value=True)
    def test_proceeds_when_validated_and_permitted(self, mocked_exists, mocked_perm):
        ec._validate_prod_requester("prod", "priya@org.com", "Sales Order", "read")  # no raise

    def test_prod_tag_name_matching_is_substring_based(self):
        # "client-a-prod" must gate too, not just an exact "prod" tag.
        with self.assertRaises(ec.UnvalidatedProdRequesterError):
            ec._validate_prod_requester("client-a-prod", None, "Sales Order", "read")


class UniversalRequesterValidationTests(unittest.TestCase):
    """RBAC pre-check, every environment: supplying a requested_by on a
    NON-PROD tag gets the same real-User + has_permission validation PROD
    always gets, so a bogus requester is never silently accepted, PROD or
    not."""

    def test_noop_on_non_prod_with_no_requester(self):
        # Presence stays optional off PROD.
        with patch.object(ec, "resource_exists") as mocked_exists:
            ec._validate_prod_requester("qa", None, "Sales Order", "read")
        mocked_exists.assert_not_called()

    @patch.object(ec, "resource_exists", return_value=False)
    def test_non_prod_refuses_unknown_user(self, mocked_exists):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("qa", "nobody@org.com", "Sales Order", "read")
        mocked_exists.assert_called_once_with("qa", "User", "nobody@org.com")
        self.assertIn("not a known ERPNext User", str(ctx.exception))

    @patch.object(ec, "check_user_permission", return_value=False)
    @patch.object(ec, "resource_exists", return_value=True)
    def test_non_prod_refuses_when_permission_check_returns_false(self, mocked_exists, mocked_perm):
        with self.assertRaises(ec.UnvalidatedProdRequesterError) as ctx:
            ec._validate_prod_requester("qa", "priya@org.com", "Sales Order", "write", docname="SO-0001")
        mocked_perm.assert_called_once_with("qa", "Sales Order", "write", "priya@org.com", "SO-0001")
        self.assertIn("does not have 'write' permission", str(ctx.exception))

    @patch.object(ec, "check_user_permission", return_value=True)
    @patch.object(ec, "resource_exists", return_value=True)
    def test_non_prod_proceeds_when_validated_and_permitted(self, mocked_exists, mocked_perm):
        ec._validate_prod_requester("qa", "priya@org.com", "Sales Order", "read")  # no raise

    def test_noop_on_exempt_doctype_on_non_prod_too(self):
        with patch.object(ec, "resource_exists") as mocked_exists:
            ec._validate_prod_requester("qa", "priya@org.com", "User", "read")
        mocked_exists.assert_not_called()


class ResolveRequestedByTests(unittest.TestCase):
    def test_cli_value_always_wins(self):
        self.assertEqual(ec.resolve_requested_by("qa", "priya@org.com", "default@org.com"), "priya@org.com")
        self.assertEqual(ec.resolve_requested_by("prod", "priya@org.com", "default@org.com"), "priya@org.com")

    def test_non_prod_falls_back_to_tag_default(self):
        self.assertEqual(ec.resolve_requested_by("qa", None, "default@org.com"), "default@org.com")
        self.assertEqual(ec.resolve_requested_by("qa", "", "default@org.com"), "default@org.com")

    def test_prod_never_falls_back_to_tag_default(self):
        self.assertEqual(ec.resolve_requested_by("prod", None, "default@org.com"), "")
        self.assertEqual(ec.resolve_requested_by("PROD_ERP", "", "default@org.com"), "")
        self.assertEqual(ec.resolve_requested_by("client-a-prod", None, "default@org.com"), "")


class RedactPiiTests(unittest.TestCase):
    def test_redacts_ssn(self):
        self.assertEqual(ec.redact_pii("my SSN is 123-45-6789 ok"), "my SSN is [REDACTED-SSN] ok")

    def test_redacts_luhn_valid_card(self):
        # 4111 1111 1111 1111 is a well-known Luhn-valid test card number.
        self.assertEqual(
            ec.redact_pii("card 4111 1111 1111 1111 please"),
            "card [REDACTED-CARD] please",
        )
        self.assertEqual(ec.redact_pii("card 4111-1111-1111-1111"), "card [REDACTED-CARD]")

    def test_does_not_redact_non_luhn_digit_runs(self):
        # 15 consecutive digits that don't pass Luhn should be left alone
        # (e.g. a PO/invoice number) — narrow by design, not general DLP.
        text = "PO number 123456789012345 attached"
        self.assertEqual(ec.redact_pii(text), text)

    def test_passthrough_on_empty_or_none(self):
        self.assertEqual(ec.redact_pii(""), "")
        self.assertIsNone(ec.redact_pii(None))

    def test_redact_pii_deep_handles_nested_structures(self):
        result = ec._redact_pii_deep({
            "a": "123-45-6789",
            "b": ["4111 1111 1111 1111", "clean text"],
            "c": {"d": "123-45-6789"},
            "e": 42,
        })
        self.assertEqual(result["a"], "[REDACTED-SSN]")
        self.assertEqual(result["b"], ["[REDACTED-CARD]", "clean text"])
        self.assertEqual(result["c"]["d"], "[REDACTED-SSN]")
        self.assertEqual(result["e"], 42)


class RecordCommentRedactsPiiTests(unittest.TestCase):
    @patch.object(ec, "_request", return_value={})
    def test_comment_content_is_redacted_before_posting(self, mocked_request):
        ec.record_comment({"tag": "qa"}, "Employee", "HR-0001",
                           "please update, my SSN is 123-45-6789")
        payload = mocked_request.call_args[1]["payload"]
        self.assertEqual(payload["content"], "please update, my SSN is [REDACTED-SSN]")


class CheckUserPermissionTests(unittest.TestCase):
    @patch.object(ec, "_request", return_value={"message": True})
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_true_response(self, mocked_cfg, mocked_request):
        result = ec.check_user_permission("prod", "Sales Order", "write", "priya@org.com", "SO-0001")
        self.assertTrue(result)
        params = mocked_request.call_args[1]["params"]
        self.assertEqual(params["doctype"], "Sales Order")
        self.assertEqual(params["perm_type"], "write")
        self.assertEqual(params["user"], "priya@org.com")
        self.assertEqual(params["docname"], "SO-0001")

    @patch.object(ec, "_request", return_value={"message": False})
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_false_response(self, mocked_cfg, mocked_request):
        self.assertFalse(ec.check_user_permission("prod", "Sales Order", "read", "priya@org.com"))

    @patch.object(ec, "_request", return_value={"message": True})
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_docname_sent_as_empty_string_when_not_given(self, mocked_cfg, mocked_request):
        # Live-confirmed against demo.qkeee.in: this Frappe build's
        # frappe.client.has_permission has no default for docname — omitting
        # the param entirely 500s. docname="" is the live-confirmed working
        # doctype-level-only shape; see check_user_permission_raw()'s docstring.
        ec.check_user_permission("prod", "Sales Order", "read", "priya@org.com")
        params = mocked_request.call_args[1]["params"]
        self.assertEqual(params["docname"], "")


class ProdGateWiringTests(unittest.TestCase):
    """Confirms the gate is actually called from every read/write entry
    point, with the right doctype/perm_type/docname — not just that the
    gate function itself works in isolation."""

    @patch.object(ec, "_validate_prod_requester")
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    @patch.object(ec, "_request", return_value={"data": []})
    def test_query_resource_gates_with_read(self, mocked_request, mocked_cfg, mocked_gate):
        ec.query_resource("prod", "Sales Order", requested_by="priya@org.com")
        mocked_gate.assert_called_once_with("prod", "priya@org.com", "Sales Order", "read")

    @patch.object(ec, "_validate_prod_requester")
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    @patch.object(ec, "_request", return_value={"data": {"name": "SO-0001"}})
    def test_get_resource_gates_with_read_and_docname(self, mocked_request, mocked_cfg, mocked_gate):
        ec.get_resource("prod", "Sales Order", "SO-0001", requested_by="priya@org.com")
        mocked_gate.assert_called_once_with("prod", "priya@org.com", "Sales Order", "read", docname="SO-0001")

    @patch.object(ec, "_validate_prod_requester")
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    @patch.object(ec, "_request", return_value={"message": {"result": []}})
    def test_run_query_report_gates_against_report_doctype(self, mocked_request, mocked_cfg, mocked_gate):
        ec.run_query_report("prod", "Sales Analytics", requested_by="priya@org.com")
        mocked_gate.assert_called_once_with("prod", "priya@org.com", "Report", "read", docname="Sales Analytics")

    @patch.object(ec, "record_audit_log_finish")
    @patch.object(ec, "record_audit_log_start", return_value="AUDITLOG-0001")
    @patch.object(ec, "_do_mutate", return_value={"data": {"name": "SO-0001"}})
    @patch.object(ec, "_validate_prod_requester")
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_mutate_resource_gates_with_action_specific_ptype(self, mocked_cfg, mocked_gate,
                                                                mocked_do_mutate, mocked_start, mocked_finish):
        ec.mutate_resource("prod", "Sales Order", "submit", name="SO-0001", mode="read-write",
                            requested_by="priya@org.com")
        mocked_gate.assert_called_once_with("prod", "priya@org.com", "Sales Order", "submit", docname="SO-0001")

    @patch.object(ec, "_validate_prod_requester", side_effect=ec.UnvalidatedProdRequesterError("nope"))
    @patch.object(ec, "get_env_config", return_value={"tag": "prod"})
    def test_mutate_resource_blocks_before_any_write_when_gate_fails(self, mocked_cfg, mocked_gate):
        with patch.object(ec, "_do_mutate") as mocked_do_mutate, \
                patch.object(ec, "record_audit_log_start") as mocked_start:
            with self.assertRaises(ec.UnvalidatedProdRequesterError):
                ec.mutate_resource("prod", "Sales Order", "create", payload={"customer": "X"},
                                    mode="read-write", requested_by="priya@org.com")
            mocked_do_mutate.assert_not_called()
            mocked_start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
