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
        self.assertEqual(cfg["api_key"], "k")

    def test_tag_sanitization(self):
        os.environ["QKEEE_ERP_CLIENT_A_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_CLIENT_A_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_CLIENT_A_QA_API_SECRET"] = "s"
        cfg = erp_client.get_env_config("client-a-qa")
        self.assertEqual(cfg["base_url"], "https://x")

    def test_list_configured_tags_requires_full_set(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        # secret missing on purpose
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
            erp_client.mutate_resource("qa", "Supplier", "create", payload={}, mode="read-only")

    def test_refuses_write_with_default_mode(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.mutate_resource("qa", "Purchase Order", "create", payload={})

    @mock.patch("erp_client.record_comment")
    @mock.patch("erp_client._request")
    def test_allows_write_in_read_write(self, mock_req, mock_comment):
        mock_req.return_value = {"data": {"name": "SUP-0001"}}
        result = erp_client.mutate_resource("qa", "Supplier", "create", payload={"supplier_name": "Acme"},
                                             mode="read-write", requested_by="priya@org.com")
        self.assertEqual(result["data"]["name"], "SUP-0001")

    @mock.patch("erp_client.record_comment")
    @mock.patch("erp_client._audit_insert", return_value=None)
    @mock.patch("erp_client._audit_update", return_value=False)
    @mock.patch("erp_client._audit_submit", return_value=False)
    @mock.patch("erp_client._request")
    def test_submit_fetches_full_doc_first(self, mock_req, mock_audit_submit, mock_audit_update,
                                            mock_audit_insert, mock_comment):
        mock_req.side_effect = [
            {"data": {"doctype": "Purchase Order", "name": "PUR-ORD-0001", "supplier": "Acme"}},
            {"message": {"doctype": "Purchase Order", "name": "PUR-ORD-0001", "docstatus": 1}},
        ]
        result = erp_client.mutate_resource("qa", "Purchase Order", "submit", name="PUR-ORD-0001",
                                             mode="read-write", requested_by="priya@org.com")
        self.assertEqual(mock_req.call_count, 2)
        self.assertEqual(result["message"]["docstatus"], 1)

    def test_update_requires_name(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.mutate_resource("qa", "Supplier", "update", payload={}, mode="read-write",
                                        requested_by="priya@org.com")

    def test_refuses_write_without_requested_by(self):
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.mutate_resource("qa", "Supplier", "create", payload={}, mode="read-write")


class TestQueryResource(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    @mock.patch("erp_client._request")
    def test_has_more_flag_when_truncated(self, mock_req):
        mock_req.return_value = {"data": [{"name": f"PO-{i}"} for i in range(21)]}
        result = erp_client.query_resource("qa", "Purchase Order", limit=20)
        self.assertEqual(len(result["data"]), 20)
        self.assertTrue(result["has_more"])

    @mock.patch("erp_client._request")
    def test_no_has_more_when_exact(self, mock_req):
        mock_req.return_value = {"data": [{"name": f"PO-{i}"} for i in range(5)]}
        result = erp_client.query_resource("qa", "Purchase Order", limit=20)
        self.assertEqual(len(result["data"]), 5)
        self.assertFalse(result["has_more"])


class TestGetUserRoles(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    @mock.patch("erp_client._request")
    def test_explicit_user(self, mock_req):
        mock_req.return_value = {"data": {"roles": [{"role": "Purchase User"}, {"role": "Purchase Manager"}]}}
        result = erp_client.get_user_roles("qa", user="jane@example.com")
        self.assertEqual(mock_req.call_count, 1)
        self.assertEqual(result["roles"], ["Purchase User", "Purchase Manager"])

    @mock.patch("erp_client._request")
    def test_resolves_current_user_when_not_given(self, mock_req):
        mock_req.side_effect = [
            {"message": "administrator"},
            {"data": {"roles": [{"role": "System Manager"}]}},
        ]
        result = erp_client.get_user_roles("qa")
        self.assertEqual(result["user"], "administrator")
        self.assertEqual(result["roles"], ["System Manager"])


if __name__ == "__main__":
    unittest.main()
