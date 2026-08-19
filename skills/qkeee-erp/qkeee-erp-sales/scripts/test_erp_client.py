#!/usr/bin/env python3
import os
import unittest

import erp_client


class TestEnvResolution(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_missing_vars_named_specifically(self):
        os.environ.pop("QKEEE_ERP_QA_BASE_URL", None)
        os.environ.pop("QKEEE_ERP_QA_API_KEY", None)
        os.environ.pop("QKEEE_ERP_QA_API_SECRET", None)
        with self.assertRaises(erp_client.ConnectorError) as ctx:
            erp_client.get_env_config("qa")
        self.assertIn("QKEEE_ERP_QA_BASE_URL", str(ctx.exception))
        self.assertIn("QKEEE_ERP_QA_API_KEY", str(ctx.exception))
        self.assertIn("QKEEE_ERP_QA_API_SECRET", str(ctx.exception))

    def test_tag_sanitization(self):
        os.environ["QKEEE_ERP_CLIENT_A_QA_BASE_URL"] = "https://x.example.com"
        os.environ["QKEEE_ERP_CLIENT_A_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_CLIENT_A_QA_API_SECRET"] = "s"
        cfg = erp_client.get_env_config("client-a-qa")
        self.assertEqual(cfg["base_url"], "https://x.example.com")

    def test_default_tag_fallback(self):
        os.environ["QKEEE_ERP_DEFAULT_BASE_URL"] = "https://d.example.com/"
        os.environ["QKEEE_ERP_DEFAULT_API_KEY"] = "k"
        os.environ["QKEEE_ERP_DEFAULT_API_SECRET"] = "s"
        cfg = erp_client.get_env_config("default")
        self.assertEqual(cfg["base_url"], "https://d.example.com")  # trailing slash stripped


class TestModeGate(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["QKEEE_ERP_DEFAULT_BASE_URL"] = "https://d.example.com"
        os.environ["QKEEE_ERP_DEFAULT_API_KEY"] = "k"
        os.environ["QKEEE_ERP_DEFAULT_API_SECRET"] = "s"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_write_refused_in_read_only(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.mutate_resource("default", "Customer", "create", payload={}, mode="read-only")

    def test_write_refused_with_default_mode_arg(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.mutate_resource("default", "Customer", "create", payload={})

    def test_update_requires_name(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.mutate_resource("default", "Customer", "update", payload={}, mode="read-write",
                                        requested_by="priya@org.com")

    def test_unknown_action_rejected(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.mutate_resource("default", "Customer", "explode", mode="read-write",
                                        requested_by="priya@org.com")

    def test_write_refused_without_requested_by(self):
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.mutate_resource("default", "Customer", "create", payload={}, mode="read-write")


class TestListConfiguredTags(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_only_complete_tag_sets_listed(self):
        os.environ["QKEEE_ERP_DEMO_BASE_URL"] = "https://demo.example.com"
        os.environ["QKEEE_ERP_DEMO_API_KEY"] = "k"
        os.environ["QKEEE_ERP_DEMO_API_SECRET"] = "s"
        os.environ["QKEEE_ERP_PARTIAL_BASE_URL"] = "https://partial.example.com"
        self.assertEqual(erp_client.list_configured_tags(), ["DEMO"])



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
