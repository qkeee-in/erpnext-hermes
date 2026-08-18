#!/usr/bin/env python3
import os
import time
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
        # _request) so the audit-logging retrofit's own _request calls
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
                 "as_of_date": "2026-04-01", "total_depreciation": 200,
                 "issued_at": int(time.time())}
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

    def test_make_depreciation_entry_rejects_stale_token(self):
        """A token computed >15 minutes ago must be refused even if it
        matches token_facts exactly — this is the freshness retrofit
        (2026-08-18): a prior version of this file had no issued_at/
        is_fresh() check at all, so a stale render could still execute."""
        stale_issued_at = int(time.time()) - 1000  # > DEFAULT_TOKEN_TTL_SECONDS (900)
        facts = {"asset": "ACC-ASS-1", "asset_depr_schedule": "ACC-ADS-1",
                 "as_of_date": "2026-04-01", "total_depreciation": 200,
                 "issued_at": stale_issued_at}
        token = depreciation_run_token(**facts)
        with mock.patch("erp_client._request") as mock_req:
            with self.assertRaises(erp_client.ConnectorError):
                erp_client.call_whitelisted_method(
                    "qa", "make_depreciation_entry",
                    {"asset_depr_schedule_name": "ACC-ADS-1"}, mode="read-write",
                    requested_by="priya@org.com",
                    confirmation_token=token, token_facts=facts,
                )
            mock_req.assert_not_called()

    def test_scrap_asset_accepts_matching_token(self):
        facts = {"asset": "ACC-ASS-1", "disposal_date": "2026-08-10", "amount": 500,
                  "issued_at": int(time.time())}
        token = disposal_token(asset="ACC-ASS-1", method="scrap",
                                disposal_date="2026-08-10", amount=500,
                                issued_at=facts["issued_at"])
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


class TestMutateResourceWithConcurrency(unittest.TestCase):
    """`mutate_resource_with_concurrency()` — this skill's own TOCTOU
    wrapper, restored 2026-08-18 after `qkeee-erp-frappe-core` syncs had twice
    silently clobbered an `expected_modified` param bolted directly onto
    `mutate_resource()` (a shared-function name). Locks in the wrapper's
    contract so a regression here fails a test instead of only surfacing
    live against a real ERPNext instance."""

    def setUp(self):
        os.environ["QKEEE_ERP_QA_BASE_URL"] = "https://x"
        os.environ["QKEEE_ERP_QA_API_KEY"] = "k"
        os.environ["QKEEE_ERP_QA_API_SECRET"] = "s"

    @mock.patch("erp_client.mutate_resource")
    @mock.patch("erp_client.get_resource")
    def test_submit_refuses_on_modified_mismatch(self, mock_get_resource, mock_mutate):
        mock_get_resource.return_value = {"data": {"modified": "2026-01-01 00:00:00.000000"}}
        with self.assertRaises(erp_client.ConnectorError) as ctx:
            erp_client.mutate_resource_with_concurrency(
                "qa", "Asset", "submit", name="ASSET-001", mode="read-write",
                expected_modified="2025-12-31 00:00:00.000000", requested_by="priya@org.com",
            )
        self.assertIn("was modified since it was last staged", str(ctx.exception))
        mock_mutate.assert_not_called()

    @mock.patch("erp_client.mutate_resource", return_value={"ok": True})
    @mock.patch("erp_client.get_resource")
    def test_submit_passes_through_on_match(self, mock_get_resource, mock_mutate):
        mock_get_resource.return_value = {"data": {"modified": "2026-01-01 00:00:00.000000"}}
        result = erp_client.mutate_resource_with_concurrency(
            "qa", "Asset", "submit", name="ASSET-001", mode="read-write",
            expected_modified="2026-01-01 00:00:00.000000", requested_by="priya@org.com",
        )
        self.assertEqual(result, {"ok": True})
        mock_mutate.assert_called_once()

    @mock.patch("erp_client.mutate_resource", return_value={"ok": True})
    @mock.patch("erp_client.get_resource")
    def test_submit_without_expected_modified_skips_check(self, mock_get_resource, mock_mutate):
        result = erp_client.mutate_resource_with_concurrency(
            "qa", "Asset", "submit", name="ASSET-001", mode="read-write", requested_by="priya@org.com",
        )
        self.assertEqual(result, {"ok": True})
        mock_get_resource.assert_not_called()
        mock_mutate.assert_called_once()

    @mock.patch("erp_client.mutate_resource", return_value={"ok": True})
    @mock.patch("erp_client.get_resource")
    def test_non_submit_action_never_checks_concurrency(self, mock_get_resource, mock_mutate):
        result = erp_client.mutate_resource_with_concurrency(
            "qa", "Asset", "create", {"asset_name": "Laptop"}, mode="read-write",
            requested_by="priya@org.com",
        )
        self.assertEqual(result, {"ok": True})
        mock_get_resource.assert_not_called()
        mock_mutate.assert_called_once()

    @mock.patch("erp_client.mutate_resource")
    def test_submit_without_name_refuses_before_checking(self, mock_mutate):
        with self.assertRaises(erp_client.ConnectorError) as ctx:
            erp_client.mutate_resource_with_concurrency(
                "qa", "Asset", "submit", mode="read-write",
                expected_modified="2026-01-01 00:00:00.000000", requested_by="priya@org.com",
            )
        self.assertIn("requires a record 'name'", str(ctx.exception))
        mock_mutate.assert_not_called()


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
