#!/usr/bin/env python3
"""Regression tests for discover.py — the resurrected schema-discovery
script (see discover.py's own module docstring and
.scratch/hermes-erp-bot-reliability/spec.md finding F8 for why it exists
again). Bare `import discover` matches this repo's convention for a
top-level scripts/ module with no __init__.py — pytest inserts the file's
own directory (scripts/) into sys.path when collecting it.

Not re-testing get_resource()/query_resource() themselves — core/
test_client.py already covers those. These tests cover the logic
discover.py adds on top: DocField-avoidance (meta goes through
get_resource(DocType, ...), never a DocField query), the _META_FIELD_KEYS
filter, and resolve_doctype()'s null-vs-error distinction for `app`.
"""

import unittest
from unittest.mock import patch

import discover


class DoctypeMetaUsesDocTypeGetResourceTests(unittest.TestCase):
    """The whole point of this script's resurrection: never query DocField
    directly (see discover.py module docstring's "why this file didn't
    exist until now"). meta must always go through get_resource() against
    DocType — the single-resource GET whose docstring in core/client.py
    says it's "the only way to get child-table rows.\""""

    @patch.object(discover, "get_resource")
    def test_meta_calls_get_resource_against_doctype_not_docfield(self, mock_get):
        mock_get.return_value = {"data": {
            "name": "Supplier", "module": "Buying", "custom": 0, "istable": 0,
            "issubmittable": 0, "description": None,
            "fields": [
                {"fieldname": "supplier_name", "label": "Supplier Name", "fieldtype": "Data",
                 "reqd": 1, "options": None, "owner": "Administrator", "idx": 1},
                {"fieldname": "gstin", "label": "GSTIN", "fieldtype": "Data", "reqd": 0,
                 "options": None, "owner": "Administrator", "idx": 7},
            ],
        }}
        result = discover.doctype_meta("DEMO_ERP", "Supplier", requested_by="user@org.com")

        called_doctype = mock_get.call_args[0][1]
        called_name = mock_get.call_args[0][2]
        self.assertEqual(called_doctype, "DocType")
        self.assertEqual(called_name, "Supplier")
        self.assertEqual(result["module"], "Buying")
        self.assertEqual(len(result["fields"]), 2)

    @patch.object(discover, "get_resource")
    def test_meta_field_filter_drops_noise_keys(self, mock_get):
        mock_get.return_value = {"data": {
            "name": "Item", "module": "Stock", "custom": 0, "istable": 0,
            "issubmittable": 0, "description": None,
            "fields": [{
                "fieldname": "gst_hsn_code", "label": "HSN/SAC", "fieldtype": "Data",
                "reqd": 0, "options": None, "owner": "Administrator", "creation": "2020-01-01",
                "modified_by": "Administrator", "idx": 42, "doctype": "DocField",
                "parentfield": "fields", "parenttype": "DocType",
            }],
        }}
        result = discover.doctype_meta("DEMO_ERP", "Item", requested_by="user@org.com")
        field = result["fields"][0]
        self.assertEqual(field, {"fieldname": "gst_hsn_code", "label": "HSN/SAC",
                                  "fieldtype": "Data", "reqd": 0, "options": None})
        self.assertNotIn("owner", field)
        self.assertNotIn("idx", field)
        self.assertNotIn("parenttype", field)


class ResolveDoctypeAppLookupTests(unittest.TestCase):
    """`app: null` must mean two different things distinguishably — see
    resolve_doctype()'s own docstring."""

    @patch.object(discover, "get_resource")
    def test_no_module_means_no_lookup_attempted(self, mock_get):
        mock_get.return_value = {"data": {"name": "X", "module": None, "custom": 1,
                                            "istable": 0, "issubmittable": 0, "fields": []}}
        result = discover.resolve_doctype("DEMO_ERP", "X", requested_by="user@org.com")
        self.assertIsNone(result["app"])
        self.assertIsNone(result["app_lookup_error"])
        mock_get.assert_called_once()  # only the DocType fetch, no Module Def fetch

    @patch.object(discover, "get_resource")
    def test_module_lookup_failure_is_distinguished_from_no_module(self, mock_get):
        def side_effect(tag, doctype, name, **kwargs):
            if doctype == "DocType":
                return {"data": {"name": "Supplier", "module": "Buying", "custom": 0,
                                  "istable": 0, "issubmittable": 0, "fields": []}}
            raise discover.ConnectorError("ERPNext API error (403) on GET /api/resource/Module Def/Buying")
        mock_get.side_effect = side_effect
        result = discover.resolve_doctype("DEMO_ERP", "Supplier", requested_by="user@org.com")
        self.assertIsNone(result["app"])
        self.assertIsNotNone(result["app_lookup_error"])
        self.assertIn("403", result["app_lookup_error"])

    @patch.object(discover, "get_resource")
    def test_successful_module_lookup_sets_app(self, mock_get):
        def side_effect(tag, doctype, name, **kwargs):
            if doctype == "DocType":
                return {"data": {"name": "Supplier", "module": "Buying", "custom": 0,
                                  "istable": 0, "issubmittable": 0, "fields": []}}
            return {"data": {"name": "Buying", "app_name": "erpnext"}}
        mock_get.side_effect = side_effect
        result = discover.resolve_doctype("DEMO_ERP", "Supplier", requested_by="user@org.com")
        self.assertEqual(result["app"], "erpnext")
        self.assertIsNone(result["app_lookup_error"])


class ListInstalledAppsFallbackTests(unittest.TestCase):
    """A blocked/whitelist-denied RPC must degrade to a named fallback
    hint, never raise past this function (the 'apps' subcommand needs a
    clean result to print either way)."""

    @patch.object(discover, "_log_read")
    @patch.object(discover, "_request")
    @patch.object(discover, "get_env_config", return_value={"tag": "DEMO_ERP"})
    @patch.object(discover, "_validate_prod_requester")
    def test_blocked_rpc_returns_fallback_hint_not_raise(self, mock_validate, mock_cfg,
                                                          mock_request, mock_log):
        mock_request.side_effect = discover.ConnectorError(
            "ERPNext API error (403) ... PermissionError: get_versions is not whitelisted"
        )
        result = discover.list_installed_apps("DEMO_ERP", requested_by="user@org.com")
        self.assertIn("error", result)
        self.assertIn("modules", result["fallback"])
        mock_log.assert_called_once()

    @patch.object(discover, "_log_read")
    @patch.object(discover, "_request")
    @patch.object(discover, "get_env_config", return_value={"tag": "DEMO_ERP"})
    @patch.object(discover, "_validate_prod_requester")
    def test_apps_call_is_gated_before_request(self, mock_validate, mock_cfg, mock_request, mock_log):
        mock_request.return_value = {"message": {"frappe": "15.0.0"}}
        discover.list_installed_apps("DEMO_ERP", requested_by="user@org.com")
        mock_validate.assert_called_once_with("DEMO_ERP", "user@org.com", "Module Def", "read")


class ListModulesLimitTests(unittest.TestCase):
    @patch.object(discover, "query_resource")
    def test_uses_large_explicit_limit_and_surfaces_has_more(self, mock_query):
        mock_query.return_value = {
            "data": [{"name": "Buying", "app_name": "erpnext"}],
            "has_more": True, "limit": 1000,
        }
        result = discover.list_modules("DEMO_ERP", requested_by="user@org.com")
        _, kwargs = mock_query.call_args
        self.assertEqual(kwargs.get("limit"), 1000)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["apps_seen_via_modules"], ["erpnext"])


if __name__ == "__main__":
    unittest.main()
