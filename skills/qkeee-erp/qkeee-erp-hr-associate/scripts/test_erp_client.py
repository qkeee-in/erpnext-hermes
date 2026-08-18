#!/usr/bin/env python3
import json
import os
import unittest
from unittest import mock

import erp_client


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

    def test_list_configured_tags_requires_full_set(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_PROD_BASE_URL"] = "https://y"
        os.environ["QKEEE_ERP_PROD_API_KEY"] = "k2"
        os.environ["QKEEE_ERP_PROD_API_SECRET"] = "s2"
        tags = erp_client.list_configured_tags()
        self.assertEqual(tags, ["PROD"])


class TestModeGate(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    def test_refuses_write_in_read_only(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.mutate_resource("qa", "Employee", "create", payload={}, mode="read-only")

    def test_refuses_write_with_default_mode(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.mutate_resource("qa", "Leave Application", "create", payload={})

    @mock.patch("erp_client.record_comment")
    @mock.patch("erp_client._request")
    def test_allows_write_in_read_write(self, mock_req, mock_comment):
        mock_req.return_value = {"data": {"name": "HR-EMP-0001"}}
        result = erp_client.mutate_resource("qa", "Employee", "create", payload={"first_name": "A"},
                                             mode="read-write", requested_by="priya@org.com")
        self.assertEqual(result["data"]["name"], "HR-EMP-0001")

    @mock.patch("erp_client.record_comment")
    @mock.patch("erp_client._audit_insert", return_value=None)
    @mock.patch("erp_client._audit_update", return_value=False)
    @mock.patch("erp_client._audit_submit", return_value=False)
    @mock.patch("erp_client._request")
    def test_submit_fetches_full_doc_first(self, mock_req, mock_audit_submit, mock_audit_update,
                                            mock_audit_insert, mock_comment):
        mock_req.side_effect = [
            {"data": {"doctype": "Leave Application", "name": "HR-LAP-0001", "status": "Approved"}},
            {"message": {"doctype": "Leave Application", "name": "HR-LAP-0001", "docstatus": 1}},
        ]
        result = erp_client.mutate_resource("qa", "Leave Application", "submit", name="HR-LAP-0001",
                                             mode="read-write", requested_by="priya@org.com")
        self.assertEqual(mock_req.call_count, 2)
        self.assertEqual(result["message"]["docstatus"], 1)

    def test_update_requires_name(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.mutate_resource("qa", "Employee", "update", payload={}, mode="read-write",
                                        requested_by="priya@org.com")

    def test_refuses_write_without_requested_by(self):
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.mutate_resource("qa", "Employee", "create", payload={}, mode="read-write")

    @mock.patch("erp_client._audit_insert", return_value=None)
    @mock.patch("erp_client._audit_update", return_value=False)
    @mock.patch("erp_client._audit_submit", return_value=False)
    @mock.patch("erp_client._request")
    def test_posts_audit_comment_naming_requester(self, mock_req, mock_audit_submit,
                                                    mock_audit_update, mock_audit_insert):
        mock_req.return_value = {"data": {"name": "HR-EMP-0001"}}
        erp_client.mutate_resource("qa", "Employee", "create", payload={"first_name": "A"},
                                    mode="read-write", requested_by="priya@org.com")
        comment_call = mock_req.call_args_list[-1]
        self.assertEqual(comment_call.args[1], "POST")
        self.assertEqual(comment_call.args[2], "/api/method/frappe.desk.form.utils.add_comment")
        self.assertIn("priya@org.com", comment_call.kwargs["payload"]["content"])


class TestQueryResource(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    @mock.patch("erp_client._request")
    def test_has_more_flag_when_truncated(self, mock_req):
        mock_req.return_value = {"data": [{"name": f"HR-EMP-{i}"} for i in range(21)]}
        result = erp_client.query_resource("qa", "Employee", limit=20)
        self.assertEqual(len(result["data"]), 20)
        self.assertTrue(result["has_more"])



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
