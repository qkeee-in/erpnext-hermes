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


if __name__ == "__main__":
    unittest.main()
