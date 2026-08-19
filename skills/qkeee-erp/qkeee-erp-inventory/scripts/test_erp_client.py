#!/usr/bin/env python3
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
            erp_client.mutate_resource("qa", "Stock Entry", "create", payload={}, mode="read-only")

    def test_refuses_write_with_default_mode(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.mutate_resource("qa", "Stock Entry", "create", payload={})

    @mock.patch("erp_client.record_comment")
    @mock.patch("erp_client._request")
    def test_allows_write_in_read_write(self, mock_req, mock_comment):
        mock_req.return_value = {"data": {"name": "MAT-STE-2026-00001"}}
        result = erp_client.mutate_resource(
            "qa", "Stock Entry", "create", payload={"stock_entry_type": "Material Receipt"},
            mode="read-write", requested_by="priya@org.com",
        )
        self.assertEqual(result["data"]["name"], "MAT-STE-2026-00001")

    @mock.patch("erp_client.record_comment")
    @mock.patch("erp_client._audit_insert", return_value=None)
    @mock.patch("erp_client._audit_update", return_value=False)
    @mock.patch("erp_client._audit_submit", return_value=False)
    @mock.patch("erp_client._request")
    def test_submit_fetches_full_doc_first(self, mock_req, mock_audit_submit, mock_audit_update,
                                            mock_audit_insert, mock_comment):
        mock_req.side_effect = [
            {"data": {"doctype": "Stock Entry", "name": "MAT-STE-2026-00001"}},
            {"message": {"doctype": "Stock Entry", "name": "MAT-STE-2026-00001", "docstatus": 1}},
        ]
        result = erp_client.mutate_resource(
            "qa", "Stock Entry", "submit", name="MAT-STE-2026-00001",
            mode="read-write", requested_by="priya@org.com",
        )
        self.assertEqual(mock_req.call_count, 2)
        self.assertEqual(result["message"]["docstatus"], 1)

    def test_update_requires_name(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.mutate_resource("qa", "Stock Entry", "update", payload={}, mode="read-write",
                                        requested_by="priya@org.com")

    def test_refuses_write_without_requested_by(self):
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.mutate_resource("qa", "Stock Entry", "create", payload={}, mode="read-write")


class TestQueryResource(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    @mock.patch("erp_client._request")
    def test_has_more_flag_when_truncated(self, mock_req):
        mock_req.return_value = {"data": [{"name": f"SKU-{i}"} for i in range(21)]}
        result = erp_client.query_resource("qa", "Item", limit=20)
        self.assertEqual(len(result["data"]), 20)
        self.assertTrue(result["has_more"])

    @mock.patch("erp_client._request")
    def test_no_has_more_when_exact(self, mock_req):
        mock_req.return_value = {"data": [{"name": f"SKU-{i}"} for i in range(5)]}
        result = erp_client.query_resource("qa", "Item", limit=20)
        self.assertEqual(len(result["data"]), 5)
        self.assertFalse(result["has_more"])


class TestGetBinQty(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    @mock.patch("erp_client._request")
    def test_scopes_to_warehouse_when_given(self, mock_req):
        mock_req.return_value = {"data": [{"item_code": "SKU-1", "warehouse": "Stores - QL", "actual_qty": 6.0}]}
        result = erp_client.get_bin_qty("qa", "SKU-1", "Stores - QL")
        self.assertEqual(result["data"][0]["actual_qty"], 6.0)
        called_params = mock_req.call_args.kwargs["params"]
        self.assertIn("Stores - QL", called_params["filters"])


class TestGetStockReconciliationItems(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    @mock.patch("erp_client._request")
    def test_flags_batch_tracked_when_batch_no_present(self, mock_req):
        mock_req.return_value = {"message": [
            {"item_code": "SKU-1", "warehouse": "Stores - QL", "current_qty": 6.0, "batch_no": "SKU1-00007"},
        ]}
        result = erp_client.get_stock_reconciliation_items(
            "qa", "Stores - QL", "Qkeee LLP", "2026-08-10", item_code="SKU-1"
        )
        self.assertTrue(result["batch_tracked"])
        self.assertEqual(len(result["rows"]), 1)

    @mock.patch("erp_client._request")
    def test_not_batch_tracked_when_no_batch_no(self, mock_req):
        mock_req.return_value = {"message": [
            {"item_code": "Raw Item-1", "warehouse": "Stores - QL", "current_qty": 0.0, "batch_no": None},
        ]}
        result = erp_client.get_stock_reconciliation_items(
            "qa", "Stores - QL", "Qkeee LLP", "2026-08-10", item_code="Raw Item-1"
        )
        self.assertFalse(result["batch_tracked"])

    @mock.patch("erp_client._request")
    def test_item_code_omitted_returns_whole_warehouse(self, mock_req):
        mock_req.return_value = {"message": [
            {"item_code": "Raw Item-1", "warehouse": "Stores - QL", "current_qty": 20.0, "batch_no": None},
            {"item_code": "SKU-1", "warehouse": "Stores - QL", "current_qty": 6.0, "batch_no": "SKU1-00007"},
        ]}
        result = erp_client.get_stock_reconciliation_items("qa", "Stores - QL", "Qkeee LLP", "2026-08-10")
        self.assertEqual(len(result["rows"]), 2)
        sent_payload = mock_req.call_args.kwargs["payload"]
        self.assertNotIn("item_code", sent_payload)


class TestBinRowsToActualSourceQty(unittest.TestCase):
    def test_converts_rows_to_tuple_keyed_dict(self):
        rows = [
            {"item_code": "SKU-1", "warehouse": "Stores - QL", "actual_qty": 6.0},
            {"item_code": "SKU-1", "warehouse": "Finished Goods - QL", "actual_qty": 4.0},
        ]
        result = erp_client.bin_rows_to_actual_source_qty(rows)
        self.assertEqual(result[("SKU-1", "Stores - QL")], 6.0)
        self.assertEqual(result[("SKU-1", "Finished Goods - QL")], 4.0)



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
