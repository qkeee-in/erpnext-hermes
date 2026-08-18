#!/usr/bin/env python3
"""
Unit tests for qkeee-erp-catch-all's erp_client.py copy — the parts that
don't need network: env resolution, tag sanitization, the mode gate, the
submit two-step, and (specific to this skill's copy) gated_mutate_resource()'s
confirmation-token gate. Run: python scripts/test_erp_client.py

Modeled on the sibling skills' test_erp_client.py (e.g. qkeee-erp-accounts-executive's).
"""

import os
import time
import unittest
from unittest import mock

from erp_client import (
    ConnectorError,
    MissingRequesterError,
    ReadOnlyModeError,
    StaleConfirmationError,
    _tag_env_var,
    gated_mutate_resource,
    get_env_config,
    list_configured_tags,
    mutate_resource,
)
from confirm_token import advisory_write_token

QA_ENV = {
    "QKEEE_ERP_QA_BASE_URL": "https://example.com",
    "QKEEE_ERP_QA_API_KEY": "key",
    "QKEEE_ERP_QA_API_SECRET": "secret",
}

# Patched in every test that reaches mutate_resource()'s audit-logging
# retrofit, so its own _request traffic (Attempted/Success rows) doesn't
# consume side_effect slots meant for the business-logic call being
# tested — same pattern the other retrofitted skills' tests use.
AUDIT_PATCHES = (
    mock.patch("erp_client.record_comment"),
    mock.patch("erp_client._audit_insert", return_value=None),
    mock.patch("erp_client._audit_update", return_value=False),
    mock.patch("erp_client._audit_submit", return_value=False),
)


def _patch_audit():
    for p in AUDIT_PATCHES:
        p.start()


def _unpatch_audit():
    for p in AUDIT_PATCHES:
        p.stop()


class TestTagEnvVar(unittest.TestCase):
    def test_sanitizes_and_uppercases(self):
        self.assertEqual(_tag_env_var("qa-1", "BASE_URL"), "QKEEE_ERP_QA_1_BASE_URL")


class TestGetEnvConfig(unittest.TestCase):
    def test_raises_with_specific_missing_var_name(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConnectorError) as ctx:
                get_env_config("qa")
            self.assertIn("QKEEE_ERP_QA_API_KEY", str(ctx.exception))

    def test_resolves_when_all_present(self):
        with mock.patch.dict(os.environ, QA_ENV, clear=True):
            cfg = get_env_config("qa")
            self.assertEqual(cfg["base_url"], "https://example.com")


class TestListConfiguredTags(unittest.TestCase):
    def test_only_complete_triples_count(self):
        env = {
            "QKEEE_ERP_QA_BASE_URL": "x", "QKEEE_ERP_QA_API_KEY": "x", "QKEEE_ERP_QA_API_SECRET": "x",
            "QKEEE_ERP_PROD_BASE_URL": "x",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(list_configured_tags(), ["QA"])


class TestMutateModeGate(unittest.TestCase):
    def test_refuses_write_in_read_only_before_any_http_call(self):
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(ReadOnlyModeError):
                mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-only")
            mocked_request.assert_not_called()

    def test_refuses_write_without_requested_by(self):
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(MissingRequesterError):
                mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write")
            mocked_request.assert_not_called()


class TestSubmitTwoStep(unittest.TestCase):
    """Same mechanic every qkeee-erp-* connector copy relies on:
    frappe.client.submit needs a DB-loaded doc, not a sparse payload, so
    mutate_resource() GETs the full record first, then POSTs it."""

    def test_submit_fetches_full_doc_then_posts_it(self):
        get_response = {"data": {"doctype": "CRM Lead", "name": "CRM-LEAD-0001", "status": "New"}}
        submit_response = {"message": {"doctype": "CRM Lead", "name": "CRM-LEAD-0001", "docstatus": 1}}
        _patch_audit()
        try:
            with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                    mock.patch("erp_client._request", side_effect=[get_response, submit_response]) as mocked:
                result = mutate_resource("qa", "CRM Lead", "submit", name="CRM-LEAD-0001",
                                          mode="read-write", requested_by="priya@org.com")
        finally:
            _unpatch_audit()

        self.assertEqual(mocked.call_count, 2)
        first_call, second_call = mocked.call_args_list
        self.assertEqual(first_call.args[1], "GET")
        self.assertEqual(second_call.args[1], "POST")
        self.assertEqual(second_call.args[2], "/api/method/frappe.client.submit")
        self.assertEqual(second_call.kwargs["payload"], {"doc": get_response["data"]})
        self.assertEqual(result, submit_response)


class TestGatedMutateResource(unittest.TestCase):
    """gated_mutate_resource() is catch-all's actual write entry point —
    this skill's own extra layer on top of mutate_resource()'s
    mode/requested_by gate (see SKILL.md step 8: advisory-first, always,
    enforced in code)."""

    def test_refuses_without_token(self):
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(ConnectorError):
                gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write",
                                       requested_by="priya@org.com")
            mocked_request.assert_not_called()

    def test_refuses_with_stale_token(self):
        old_issued_at = int(time.time()) - 10_000  # well past DEFAULT_TOKEN_TTL_SECONDS
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", old_issued_at)
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(StaleConfirmationError):
                gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-write",
                                       requested_by="priya@org.com",
                                       confirmation_token=token, issued_at=old_issued_at)
            mocked_request.assert_not_called()

    def test_refuses_with_mismatched_payload(self):
        """The rendered token is bound to the exact payload — a caller
        can't render one draft and execute a different one under the
        same token."""
        issued_at = int(time.time())
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", issued_at)
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(ConnectorError):
                gated_mutate_resource("qa", "CRM Lead", "create", {"x": 2}, mode="read-write",
                                       requested_by="priya@org.com",
                                       confirmation_token=token, issued_at=issued_at)
            mocked_request.assert_not_called()

    def test_succeeds_with_matching_fresh_token(self):
        issued_at = int(time.time())
        payload = {"lead_name": "Acme"}
        token = advisory_write_token("create", "CRM Lead", None, payload, "priya@org.com", issued_at)
        _patch_audit()
        try:
            with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                    mock.patch("erp_client._request",
                                return_value={"data": {"name": "CRM-LEAD-0001"}}) as mocked:
                result = gated_mutate_resource("qa", "CRM Lead", "create", payload, mode="read-write",
                                                requested_by="priya@org.com",
                                                confirmation_token=token, issued_at=issued_at)
        finally:
            _unpatch_audit()
        self.assertEqual(result["data"]["name"], "CRM-LEAD-0001")
        mocked.assert_called_once()

    def test_still_refuses_read_only_even_with_valid_token(self):
        """The token gate is additive, not a replacement for the
        mode/requested_by gate mutate_resource() already enforces."""
        issued_at = int(time.time())
        token = advisory_write_token("create", "CRM Lead", None, {"x": 1}, "priya@org.com", issued_at)
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(ReadOnlyModeError):
                gated_mutate_resource("qa", "CRM Lead", "create", {"x": 1}, mode="read-only",
                                       requested_by="priya@org.com",
                                       confirmation_token=token, issued_at=issued_at)
            mocked_request.assert_not_called()



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

if __name__ == "__main__":
    unittest.main()
