#!/usr/bin/env python3
"""
Unit tests for erp_client.py's pure/offline-testable functions and the
read-only/requested-by/action gates. Run: python scripts/test_erp_client.py

This connector copy has never been exercised live against a real ERPNext
instance (see qkeee-erp-skills-library-plan.md's adversarial review) —
these tests cover what's verifiable offline: env resolution,
tag sanitization, and every gate refusing before any HTTP call is made.
They are not a substitute for the live dry-run/real-run validation noted
in SKILL.md.
"""

import os
import unittest
from unittest import mock

from erp_client import (
    AUDIT_EXEMPT_DOCTYPES,
    ConnectorError,
    MissingRequesterError,
    ReadOnlyModeError,
    _tag_env_var,
    get_env_config,
    list_configured_tags,
    mutate_resource,
    resource_exists,
)

QA_ENV = {
    "QKEEE_ERP_QA_BASE_URL": "https://example.com",
    "QKEEE_ERP_QA_API_KEY": "key",
    "QKEEE_ERP_QA_API_SECRET": "secret",
}


class TestTagEnvVar(unittest.TestCase):
    def test_sanitizes_and_uppercases(self):
        self.assertEqual(_tag_env_var("qa-1", "BASE_URL"), "QKEEE_ERP_QA_1_BASE_URL")


class TestGetEnvConfig(unittest.TestCase):
    def test_raises_with_specific_missing_var_name(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConnectorError) as ctx:
                get_env_config("qa")
            self.assertIn("QKEEE_ERP_QA_API_KEY", str(ctx.exception))

    def test_resolves_when_all_present(self):
        with mock.patch.dict(os.environ, QA_ENV, clear=True):
            cfg = get_env_config("qa")
            self.assertEqual(cfg["base_url"], "https://example.com")


class TestListConfiguredTags(unittest.TestCase):
    def test_only_complete_triples_count(self):
        env = {
            "QKEEE_ERP_QA_BASE_URL": "x", "QKEEE_ERP_QA_API_KEY": "x", "QKEEE_ERP_QA_API_SECRET": "x",
            "QKEEE_ERP_PROD_BASE_URL": "x",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(list_configured_tags(), ["QA"])


class TestResourceExists(unittest.TestCase):
    def test_true_when_get_succeeds(self):
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("erp_client._request", return_value={"data": {"name": "DocType"}}):
            self.assertTrue(resource_exists("qa", "DocType", "Qkeee Bot Persona"))

    def test_false_on_404(self):
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("erp_client._request", side_effect=ConnectorError("ERPNext API error (404) ...")):
            self.assertFalse(resource_exists("qa", "DocType", "Qkeee Bot Persona"))

    def test_reraises_non_404_errors(self):
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("erp_client._request", side_effect=ConnectorError("ERPNext API error (403) ...")):
            with self.assertRaises(ConnectorError):
                resource_exists("qa", "DocType", "Qkeee Bot Persona")


class TestMutateGates(unittest.TestCase):
    def test_refuses_write_in_read_only_before_any_http_call(self):
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(ReadOnlyModeError):
                mutate_resource("qa", "DocType", "create", {"x": 1}, mode="read-only", requested_by="admin@org.com")
            mocked_request.assert_not_called()

    def test_refuses_write_without_requested_by(self):
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(MissingRequesterError):
                mutate_resource("qa", "DocType", "create", {"x": 1}, mode="read-write")
            mocked_request.assert_not_called()

    def test_refuses_submit_action_this_skill_never_uses(self):
        """Divergence from qkeee-erp-core's connector: this copy only ever
        creates/updates DocType/Role records, never submits/cancels/deletes."""
        with mock.patch("erp_client._request") as mocked_request:
            with self.assertRaises(ConnectorError):
                mutate_resource("qa", "DocType", "submit", name="X",
                                 mode="read-write", requested_by="admin@org.com")
            mocked_request.assert_not_called()

    def test_allows_create_in_read_write_mode(self):
        with mock.patch.dict(os.environ, QA_ENV, clear=True), \
                mock.patch("erp_client.record_comment"), \
                mock.patch("erp_client._audit_insert", return_value=None), \
                mock.patch("erp_client._audit_update", return_value=False), \
                mock.patch("erp_client._audit_submit", return_value=False), \
                mock.patch("erp_client._request", return_value={"data": {"name": "Qkeee Bot Persona"}}) as mocked:
            result = mutate_resource("qa", "DocType", "create", {"name": "Qkeee Bot Persona"},
                                      mode="read-write", requested_by="admin@org.com")
        self.assertEqual(result["data"]["name"], "Qkeee Bot Persona")
        mocked.assert_called_once()


class TestAuditExemptDoctypes(unittest.TestCase):
    def test_audit_doctypes_and_comment_are_exempt(self):
        for dt in ("Qkeee Bot Audit Log", "Qkeee Bot Session", "Qkeee Bot Message",
                   "Qkeee Bot Persona", "Comment"):
            self.assertIn(dt, AUDIT_EXEMPT_DOCTYPES)

    def test_doctype_and_role_are_not_exempt(self):
        """This skill's own writes (DocType/Role create) are fair game for
        audit logging once Qkeee Bot Audit Log exists on the target — see
        erp_client.py's module docstring."""
        self.assertNotIn("DocType", AUDIT_EXEMPT_DOCTYPES)
        self.assertNotIn("Role", AUDIT_EXEMPT_DOCTYPES)


if __name__ == "__main__":
    unittest.main()
