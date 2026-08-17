#!/usr/bin/env python3
import os
import unittest
from unittest import mock

import erp_client
from confirm_token import depreciation_run_token, disposal_token


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
            erp_client.mutate_resource("qa", "Asset", "create", payload={}, mode="read-only")

    def test_refuses_write_with_default_mode(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.mutate_resource("qa", "Asset", "create", payload={})

    @mock.patch("erp_client.record_comment")
    @mock.patch("erp_client._request")
    def test_allows_write_in_read_write(self, mock_req, mock_comment):
        mock_req.return_value = {"data": {"name": "ACC-ASS-2026-00001"}}
        result = erp_client.mutate_resource(
            "qa", "Asset", "create", payload={"asset_name": "Laptop"}, mode="read-write",
            requested_by="priya@org.com",
        )
        self.assertEqual(result["data"]["name"], "ACC-ASS-2026-00001")

    @mock.patch("erp_client.record_comment")
    @mock.patch("erp_client._audit_insert", return_value=None)
    @mock.patch("erp_client._audit_update", return_value=False)
    @mock.patch("erp_client._audit_submit", return_value=False)
    @mock.patch("erp_client._request")
    def test_submit_fetches_full_doc_first(self, mock_req, mock_audit_submit, mock_audit_update,
                                            mock_audit_insert, mock_comment):
        # _audit_insert/_audit_update/_audit_submit are patched directly (not just
        # _request) so the 2026-08-16 audit-logging retrofit's own _request calls
        # (Attempted-row insert before the write, Success-row update+submit after)
        # don't consume slots from this test's ordered side_effect list, which is
        # written to match only the real submit flow's two calls.
        mock_req.side_effect = [
            {"data": {"doctype": "Asset", "name": "ACC-ASS-2026-00001"}},
            {"message": {"doctype": "Asset", "name": "ACC-ASS-2026-00001", "docstatus": 1}},
        ]
        result = erp_client.mutate_resource(
            "qa", "Asset", "submit", name="ACC-ASS-2026-00001", mode="read-write",
            requested_by="priya@org.com",
        )
        self.assertEqual(mock_req.call_count, 2)
        self.assertEqual(result["message"]["docstatus"], 1)

    def test_update_requires_name(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.mutate_resource("qa", "Asset", "update", payload={}, mode="read-write",
                                        requested_by="priya@org.com")

    def test_refuses_write_without_requested_by(self):
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.mutate_resource("qa", "Asset", "create", payload={}, mode="read-write")


class TestCallWhitelistedMethod(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    def test_refuses_in_read_only(self):
        with self.assertRaises(erp_client.ReadOnlyModeError):
            erp_client.call_whitelisted_method(
                "qa", "restore_asset", {"asset_name": "ACC-ASS-1"}, mode="read-only"
            )

    def test_unknown_method_raises(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.call_whitelisted_method("qa", "bogus_method", {}, mode="read-write")

    def test_refuses_without_requested_by(self):
        with self.assertRaises(erp_client.MissingRequesterError):
            erp_client.call_whitelisted_method(
                "qa", "restore_asset", {"asset_name": "ACC-ASS-1"}, mode="read-write"
            )

    def test_restore_asset_not_token_gated(self):
        with mock.patch("erp_client._request") as mock_req:
            mock_req.return_value = {"message": {}}
            erp_client.call_whitelisted_method(
                "qa", "restore_asset", {"asset_name": "ACC-ASS-1"}, mode="read-write",
                requested_by="priya@org.com",
            )
            # restore_asset call + the best-effort audit comment on ACC-ASS-1
            self.assertEqual(mock_req.call_count, 2)

    def test_make_depreciation_entry_requires_token(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.call_whitelisted_method(
                "qa", "make_depreciation_entry",
                {"asset_depr_schedule_name": "ACC-ADS-1"}, mode="read-write",
                requested_by="priya@org.com",
            )

    def test_make_depreciation_entry_rejects_mismatched_token(self):
        with self.assertRaises(erp_client.ConnectorError):
            erp_client.call_whitelisted_method(
                "qa", "make_depreciation_entry",
                {"asset_depr_schedule_name": "ACC-ADS-1"}, mode="read-write",
                requested_by="priya@org.com",
                confirmation_token="not-a-real-token",
                token_facts={"asset": "ACC-ASS-1", "asset_depr_schedule": "ACC-ADS-1",
                             "as_of_date": "2026-04-01", "total_depreciation": 200},
            )

    def test_make_depreciation_entry_accepts_matching_token(self):
        facts = {"asset": "ACC-ASS-1", "asset_depr_schedule": "ACC-ADS-1",
                 "as_of_date": "2026-04-01", "total_depreciation": 200}
        token = depreciation_run_token(**facts)
        with mock.patch("erp_client._request") as mock_req:
            mock_req.return_value = {"message": {}}
            erp_client.call_whitelisted_method(
                "qa", "make_depreciation_entry",
                {"asset_depr_schedule_name": "ACC-ADS-1"}, mode="read-write",
                requested_by="priya@org.com",
                confirmation_token=token, token_facts=facts,
            )
            # token_facts must never leak into the actual API payload
            sent_payload = mock_req.call_args.kwargs.get("payload") or mock_req.call_args[0][3]
            self.assertEqual(sent_payload, {"asset_depr_schedule_name": "ACC-ADS-1"})
            # no "asset_name" key in this RPC's body -> no audit comment attempted
            mock_req.assert_called_once()

    def test_scrap_asset_accepts_matching_token(self):
        facts = {"asset": "ACC-ASS-1", "disposal_date": "2026-08-10", "amount": 500}
        token = disposal_token(asset="ACC-ASS-1", method="scrap",
                                disposal_date="2026-08-10", amount=500)
        with mock.patch("erp_client._request") as mock_req:
            mock_req.return_value = {"_server_messages": "[]"}
            erp_client.call_whitelisted_method(
                "qa", "scrap_asset", {"asset_name": "ACC-ASS-1"}, mode="read-write",
                requested_by="priya@org.com",
                confirmation_token=token, token_facts=facts,
            )
            # scrap_asset call + the best-effort audit comment on ACC-ASS-1
            self.assertEqual(mock_req.call_count, 2)
            comment_call = mock_req.call_args_list[-1]
            self.assertEqual(comment_call.args[2], "/api/method/frappe.desk.form.utils.add_comment")
            self.assertIn("priya@org.com", comment_call.kwargs["payload"]["content"])


class TestQueryResource(unittest.TestCase):
    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    @mock.patch("erp_client._request")
    def test_has_more_flag_when_truncated(self, mock_req):
        mock_req.return_value = {"data": [{"name": f"ACC-ASS-{i}"} for i in range(21)]}
        result = erp_client.query_resource("qa", "Asset", limit=20)
        self.assertEqual(len(result["data"]), 20)
        self.assertTrue(result["has_more"])

    @mock.patch("erp_client._request")
    def test_no_has_more_when_exact(self, mock_req):
        mock_req.return_value = {"data": [{"name": f"ACC-ASS-{i}"} for i in range(5)]}
        result = erp_client.query_resource("qa", "Asset", limit=20)
        self.assertEqual(len(result["data"]), 5)
        self.assertFalse(result["has_more"])


if __name__ == "__main__":
    unittest.main()
