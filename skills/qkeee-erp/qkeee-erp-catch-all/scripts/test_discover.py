#!/usr/bin/env python3
"""
Unit tests for discover.py — the offline-testable parts: field-shape
extraction, the apps/modules fallback, and resolve_doctype()'s app vs.
app_lookup_error distinction. Run: python scripts/test_discover.py

discover.py resolve/meta were exercised live against demo.qkeee.in
(session-cookie auth — token-key minting was blocked on that
instance at the time) — see references/connector-reference.md and the
qkeee-erp-demo-instance memory entry for that record. These tests cover
what doesn't need network.
"""

import os
import unittest
from unittest import mock

from discover import doctype_meta, list_installed_apps, list_modules, resolve_doctype
from erp_client import ConnectorError

QA_ENV = {
    "QKEEE_ERP_QA_BASE_URL": "https://example.com",
    "QKEEE_ERP_QA_API_KEY": "key",
    "QKEEE_ERP_QA_API_SECRET": "secret",
}

CRM_LEAD_DOCTYPE_DOC = {
    "data": {
        "name": "CRM Lead",
        "module": "FCRM",
        "custom": 0,
        "istable": 0,
        "issubmittable": 0,
        "description": "A sales lead",
        "fields": [
            {"fieldname": "lead_name", "label": "Lead Name", "fieldtype": "Data", "reqd": 1,
             "options": None, "read_only": 0, "hidden": 0, "default": None, "unique": 0,
             "in_list_view": 1, "permlevel": 0, "some_noise_field": "dropped"},
            {"fieldname": "status", "label": "Status", "fieldtype": "Link", "reqd": 1,
             "options": "CRM Lead Status"},
        ],
    }
}


class TestDoctypeMeta(unittest.TestCase):
    def test_strips_to_known_field_keys_only(self):
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("discover._request", return_value=CRM_LEAD_DOCTYPE_DOC), \
                mock.patch("discover._log_read"):
            result = doctype_meta("qa", "CRM Lead")
        self.assertEqual(result["doctype"], "CRM Lead")
        self.assertEqual(result["module"], "FCRM")
        self.assertFalse(result["custom"])
        lead_name_field = next(f for f in result["fields"] if f["fieldname"] == "lead_name")
        self.assertNotIn("permlevel", lead_name_field)
        self.assertNotIn("some_noise_field", lead_name_field)
        self.assertEqual(lead_name_field["reqd"], 1)

    def test_debug_true_logs_read(self):
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("discover._request", return_value=CRM_LEAD_DOCTYPE_DOC), \
                mock.patch("discover._log_read") as mocked_log:
            doctype_meta("qa", "CRM Lead", debug=True, requested_by="priya@org.com")
        mocked_log.assert_called_once()

    def test_debug_false_does_not_log(self):
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("discover._request", return_value=CRM_LEAD_DOCTYPE_DOC), \
                mock.patch("discover._log_read") as mocked_log:
            doctype_meta("qa", "CRM Lead", debug=False)
        mocked_log.assert_not_called()


class TestResolveDoctype(unittest.TestCase):
    """app=null must be
    distinguishable from "lookup failed" vs "genuinely nothing to
    resolve" — previously both collapsed to a bare app: null."""

    def test_resolves_app_from_module(self):
        module_def_doc = {"data": {"name": "FCRM", "app_name": "crm"}}
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("discover._request",
                            side_effect=[CRM_LEAD_DOCTYPE_DOC, module_def_doc]), \
                mock.patch("discover._log_read"):
            result = resolve_doctype("qa", "CRM Lead")
        self.assertEqual(result["app"], "crm")
        self.assertIsNone(result["app_lookup_error"])

    def test_no_module_reports_no_lookup_error(self):
        no_module_doc = {"data": {**CRM_LEAD_DOCTYPE_DOC["data"], "module": None}}
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("discover._request", return_value=no_module_doc), \
                mock.patch("discover._log_read"):
            result = resolve_doctype("qa", "CRM Lead")
        self.assertIsNone(result["app"])
        self.assertIsNone(result["app_lookup_error"])
        self.assertIsNone(result["module"])

    def test_failed_module_lookup_reports_error_not_silent_none(self):
        """Before this fix: a ConnectorError on the Module Def GET was
        swallowed and app silently came back None, indistinguishable from
        'confirmed no owning app'."""
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("discover._request",
                            side_effect=[CRM_LEAD_DOCTYPE_DOC,
                                         ConnectorError("ERPNext API error (403) on GET ...")]), \
                mock.patch("discover._log_read"):
            result = resolve_doctype("qa", "CRM Lead")
        self.assertIsNone(result["app"])
        self.assertIsNotNone(result["app_lookup_error"])
        self.assertIn("403", result["app_lookup_error"])


class TestListModules(unittest.TestCase):
    def test_derives_apps_seen_from_module_rows(self):
        module_rows = {"data": [
            {"name": "FCRM", "app_name": "crm"},
            {"name": "HR", "app_name": "hrms"},
            {"name": "Core", "app_name": "frappe"},
        ]}
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("discover._request", return_value=module_rows), \
                mock.patch("discover._log_read"):
            result = list_modules("qa")
        self.assertEqual(result["apps_seen_via_modules"], ["crm", "frappe", "hrms"])


class TestListInstalledApps(unittest.TestCase):
    def test_confirmed_blocked_rpc_reports_fallback_not_a_crash(self):
        """Reproduces the real finding against demo.qkeee.in:
        frappe.utils.change_log.get_versions came back
        'PermissionError: ... is not whitelisted'. list_installed_apps()
        must surface that as a normal error+fallback dict, not raise."""
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("discover._request",
                            side_effect=ConnectorError(
                                "ERPNext API error (403) on GET /api/method/"
                                "frappe.utils.change_log.get_versions: PermissionError: "
                                "... is not whitelisted")):
            result = list_installed_apps("qa")
        self.assertIn("error", result)
        self.assertIn("fallback", result)
        self.assertIn("modules", result["fallback"])


if __name__ == "__main__":
    unittest.main()
