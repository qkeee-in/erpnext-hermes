#!/usr/bin/env python3
"""
Unit tests for erp_client.py's pure/offline-testable functions and the
read-only mode gate. Run: python scripts/test_erp_client.py

health_check()/query_resource()/run_query_report()/mutate_resource()
were exercised live against <erp-instance> during this skill's build
(create -> submit -> cancel Journal Entry round trip) — see
references/connector-reference.md for that record. These tests cover
the parts that don't need network: env resolution, tag sanitization,
and the mode gate refusing before any HTTP call is made.
"""

import os
import unittest
from unittest import mock

from erp_client import (
    ConnectorError,
    MissingRequesterError,
    ReadOnlyModeError,
    _tag_env_var,
    get_env_config,
    list_configured_tags,
    mutate_resource,
    run_query_report,
)

QA_ENV = {
    "QKEEE_ERP_QA_BASE_URL": "https://example.com",
    "QKEEE_ERP_QA_API_KEY": "key",
    "QKEEE_ERP_QA_API_SECRET": "secret",
}


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
        env = {
            "QKEEE_ERP_QA_BASE_URL": "https://example.com/",
            "QKEEE_ERP_QA_API_KEY": "key",
            "QKEEE_ERP_QA_API_SECRET": "secret",
        }
        with mock.patch.dict(os.environ, env, clear=True):
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
                mutate_resource("qa", "Journal Entry", "create", {"x": 1}, mode="read-only")
            mocked_request.assert_not_called()

    def test_allows_write_in_read_write_mode(self):
        env = {
            "QKEEE_ERP_QA_BASE_URL": "https://example.com",
            "QKEEE_ERP_QA_API_KEY": "key",
            "QKEEE_ERP_QA_API_SECRET": "secret",
        }
        # _audit_insert/_audit_update/_audit_submit are patched directly (not
        # just _request) so the audit-logging retrofit's own
        # _request calls don't count against this test's assert_called_once()
        # on the real business _request call.
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("erp_client.record_comment"), \
                mock.patch("erp_client._audit_insert", return_value=None), \
                mock.patch("erp_client._audit_update", return_value=False), \
                mock.patch("erp_client._audit_submit", return_value=False), \
                mock.patch("erp_client._request", return_value={"data": {"name": "JE-0001"}}) as mocked:
            result = mutate_resource("qa", "Journal Entry", "create", {"x": 1}, mode="read-write",
                                      requested_by="priya@org.com")
        self.assertEqual(result["data"]["name"], "JE-0001")
        mocked.assert_called_once()

    def test_refuses_write_without_requested_by(self):
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(MissingRequesterError):
                mutate_resource("qa", "Journal Entry", "create", {"x": 1}, mode="read-write")
            mocked_request.assert_not_called()


class TestSubmitTwoStep(unittest.TestCase):
    """submit is the connector's trickiest path: GET the full doc first
    (frappe.client.submit needs a DB-loaded doc, not a sparse payload —
    see erp_client.py's mutate_resource docstring), then POST it. This is
    exactly the mechanic that was previously found broken in
    qkeee-erp-frappe-core's build; regression-test it here rather than relying
    solely on the one-time live validation record."""

    def test_submit_fetches_full_doc_then_posts_it(self):
        get_response = {"data": {"doctype": "Journal Entry", "name": "ACC-JV-2026-00001", "total_debit": 100}}
        submit_response = {"message": {"doctype": "Journal Entry", "name": "ACC-JV-2026-00001", "docstatus": 1}}
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("erp_client.record_comment"), \
                mock.patch("erp_client._audit_insert", return_value=None), \
                mock.patch("erp_client._audit_update", return_value=False), \
                mock.patch("erp_client._audit_submit", return_value=False), \
                mock.patch("erp_client._request", side_effect=[get_response, submit_response]) as mocked:
            result = mutate_resource("qa", "Journal Entry", "submit", name="ACC-JV-2026-00001",
                                      mode="read-write", requested_by="priya@org.com")

        self.assertEqual(mocked.call_count, 2)
        first_call, second_call = mocked.call_args_list
        self.assertEqual(first_call.args[1], "GET")
        self.assertIn("ACC-JV-2026-00001", first_call.args[2])
        self.assertEqual(second_call.args[1], "POST")
        self.assertEqual(second_call.args[2], "/api/method/frappe.client.submit")
        # The full doc fetched via GET (not a sparse {doctype, name} payload)
        # must be what gets posted to frappe.client.submit.
        self.assertEqual(second_call.kwargs["payload"], {"doc": get_response["data"]})
        # submit's response is shaped {"message": ...}, not {"data": ...} —
        # mutate_resource must not normalize/unwrap this; callers branch on it.
        self.assertEqual(result, submit_response)

    def test_submit_raises_specific_error_when_record_not_found(self):
        get_response_empty = {}
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("erp_client._request", return_value=get_response_empty):
            with self.assertRaisesRegex(ConnectorError, "Could not load"):
                mutate_resource("qa", "Journal Entry", "submit", name="ACC-JV-2026-00001",
                                 mode="read-write", requested_by="priya@org.com")

    def test_cancel_does_not_fetch_first(self):
        cancel_response = {"message": {"doctype": "Journal Entry", "name": "ACC-JV-2026-00001", "docstatus": 2}}
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("erp_client.record_comment"), \
                mock.patch("erp_client._audit_insert", return_value=None), \
                mock.patch("erp_client._audit_update", return_value=False), \
                mock.patch("erp_client._audit_submit", return_value=False), \
                mock.patch("erp_client._request", return_value=cancel_response) as mocked:
            result = mutate_resource("qa", "Journal Entry", "cancel", name="ACC-JV-2026-00001",
                                      mode="read-write", requested_by="priya@org.com")

        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[1], "POST")
        self.assertEqual(mocked.call_args.args[2], "/api/method/frappe.client.cancel")
        self.assertEqual(result, cancel_response)


class TestRunQueryReport(unittest.TestCase):
    def test_posts_report_name_and_filters(self):
        env = {
            "QKEEE_ERP_QA_BASE_URL": "https://example.com",
            "QKEEE_ERP_QA_API_KEY": "key",
            "QKEEE_ERP_QA_API_SECRET": "secret",
        }
        fake_response = {"message": {"columns": [{"fieldname": "party"}], "result": [{"party": "Acme"}]}}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("erp_client._request", return_value=fake_response) as mocked:
            out = run_query_report("qa", "Accounts Receivable", {"company": "Acme"})
        self.assertEqual(out["report_name"], "Accounts Receivable")
        self.assertEqual(out["result"], [{"party": "Acme"}])
        # GET + query-string params, not POST — confirmed live (2026-08-18
        # core sync: see qkeee-erp-frappe-core/references/connector-reference.md
        # "Built-in reports vs. hand-aggregated queries").
        self.assertEqual(mocked.call_args[0][1], "GET")
        self.assertEqual(mocked.call_args.kwargs["params"]["report_name"], "Accounts Receivable")



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
