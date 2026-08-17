#!/usr/bin/env python3
"""
Unit tests for erp_client.py's pure/offline-testable functions.
Run: python scripts/test_erp_client.py

Deliberately does NOT test health_check()/query_resource() against a
real ERPNext instance — no such instance is available in this build
environment. Those two remain unverified end-to-end for this
hand-rewritten (not mechanically copied) read-only-only client; confirm
them against a live instance before treating this connector as fully
field-validated, same caveat qkeee-erp-core's own reference notes for
its canonical copy.
"""

import os
import unittest
from unittest import mock

from erp_client import ConnectorError, _tag_env_var, get_env_config, list_configured_tags, run_query_report


class TestTagEnvVar(unittest.TestCase):
    def test_sanitizes_and_uppercases(self):
        self.assertEqual(_tag_env_var("qa-1", "BASE_URL"), "QKEEE_ERP_QA_1_BASE_URL")

    def test_empty_tag_falls_back_to_default(self):
        self.assertEqual(_tag_env_var("", "BASE_URL"), "QKEEE_ERP_DEFAULT_BASE_URL")


class TestGetEnvConfig(unittest.TestCase):
    def test_raises_with_specific_missing_var_name(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConnectorError) as ctx:
                get_env_config("qa")
            self.assertIn("QKEEE_ERP_QA_BASE_URL", str(ctx.exception))
            self.assertIn("QKEEE_ERP_QA_API_KEY", str(ctx.exception))
            self.assertIn("QKEEE_ERP_QA_API_SECRET", str(ctx.exception))

    def test_resolves_when_all_present(self):
        env = {
            "QKEEE_ERP_QA_BASE_URL": "https://example.com/",
            "QKEEE_ERP_QA_API_KEY": "key",
            "QKEEE_ERP_QA_API_SECRET": "secret",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = get_env_config("qa")
            self.assertEqual(cfg["base_url"], "https://example.com")  # trailing slash stripped


class TestListConfiguredTags(unittest.TestCase):
    def test_only_complete_triples_count(self):
        env = {
            "QKEEE_ERP_QA_BASE_URL": "x",
            "QKEEE_ERP_QA_API_KEY": "x",
            "QKEEE_ERP_QA_API_SECRET": "x",
            "QKEEE_ERP_PROD_BASE_URL": "x",  # incomplete — missing key/secret
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(list_configured_tags(), ["QA"])


class TestRunQueryReport(unittest.TestCase):
    def test_posts_report_name_and_filters_extracts_message(self):
        env = {
            "QKEEE_ERP_QA_BASE_URL": "https://example.com",
            "QKEEE_ERP_QA_API_KEY": "key",
            "QKEEE_ERP_QA_API_SECRET": "secret",
        }
        fake_response = {"message": {"columns": [{"fieldname": "account"}], "result": [{"account": "Cash"}]}}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("erp_client._request", return_value=fake_response) as mocked:
            out = run_query_report("qa", "Trial Balance", {"company": "Acme"})
        self.assertEqual(out["report_name"], "Trial Balance")
        self.assertEqual(out["columns"], [{"fieldname": "account"}])
        self.assertEqual(out["result"], [{"account": "Cash"}])
        called_kwargs = mocked.call_args.kwargs
        self.assertEqual(called_kwargs["payload"]["report_name"], "Trial Balance")
        self.assertEqual(called_kwargs["payload"]["filters"], {"company": "Acme"})


if __name__ == "__main__":
    unittest.main()
