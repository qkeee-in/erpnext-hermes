#!/usr/bin/env python3
"""Regression tests for the audit-logging silent-failure bug: session_id
missing must never produce an empty `session` field on Qkeee Bot Audit Log
(that field is mandatory, and _audit_insert() swallows the resulting
MandatoryError silently — see bot-doctypes-design.md decision 10)."""

import unittest
from unittest.mock import patch

import erp_client as ec


class SessionFallbackTests(unittest.TestCase):
    def test_session_or_fallback_passthrough(self):
        self.assertEqual(ec._session_or_fallback("sess-123"), "sess-123")

    def test_session_or_fallback_generates_nonempty_id(self):
        for missing in (None, ""):
            with self.subTest(missing=missing):
                result = ec._session_or_fallback(missing)
                self.assertTrue(result)
                self.assertTrue(result.startswith("local-"))

    @patch.object(ec, "_audit_insert")
    def test_log_read_never_sends_empty_session(self, mock_insert):
        ec._log_read(
            {"tag": "default"}, "Sales Order", "SO-0001",
            requested_by="user@example.com", session_id=None, persona_code=None,
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertTrue(sent_fields["session"])

    @patch.object(ec, "_audit_insert")
    def test_record_audit_log_start_never_sends_empty_session(self, mock_insert):
        ec.record_audit_log_start(
            {"tag": "default"}, action="Create", doctype="Sales Order", name=None,
            requested_by="user@example.com", session_id=None,
        )
        sent_fields = mock_insert.call_args[0][1]
        self.assertTrue(sent_fields["session"])


class AuditInsertFailureVisibilityTests(unittest.TestCase):
    @patch.object(ec, "_request", side_effect=RuntimeError("boom"))
    def test_audit_insert_warns_on_stderr_and_returns_none(self, mock_request):
        with patch("sys.stderr") as mock_stderr:
            result = ec._audit_insert({"tag": "default"}, {"session": "s1"})
        self.assertIsNone(result)
        self.assertTrue(mock_stderr.write.called)


class EnsurePersonaRegisteredTests(unittest.TestCase):
    """ensure_persona_registered() is unconditional master-data upsert —
    never a log, never gated on debug. Covers the three real outcomes: the
    persona row already exists (no-op), it doesn't and gets created, and the
    target instance hasn't provisioned Qkeee Bot Persona at all (swallowed
    failure, never raises)."""

    @patch.object(ec, "resource_exists", return_value=True)
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_request")
    def test_noop_when_already_registered(self, mock_request, mock_get_env_config, mock_resource_exists):
        ec.ensure_persona_registered(
            "default", persona_code="qkeee-erp-sales", persona_label="Sales",
        )
        mock_resource_exists.assert_called_once_with("default", ec.PERSONA_DOCTYPE, "qkeee-erp-sales")
        mock_request.assert_not_called()

    @patch.object(ec, "resource_exists", return_value=False)
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_request")
    def test_creates_when_not_registered(self, mock_request, mock_get_env_config, mock_resource_exists):
        ec.ensure_persona_registered(
            "default", persona_code="qkeee-erp-sales", persona_label="Sales",
            default_mode="read-write", non_negotiables="never bulk-delete",
        )
        mock_request.assert_called_once()
        cfg_arg, method, path = mock_request.call_args[0][:3]
        self.assertEqual(cfg_arg, {"tag": "default"})
        self.assertEqual(method, "POST")
        self.assertIn(ec.PERSONA_DOCTYPE.replace(" ", "%20"), path)
        payload = mock_request.call_args[1]["payload"]
        self.assertEqual(payload["doctype"], ec.PERSONA_DOCTYPE)
        self.assertEqual(payload["persona_code"], "qkeee-erp-sales")
        self.assertEqual(payload["persona_label"], "Sales")
        self.assertEqual(payload["default_mode"], "Read Write")
        self.assertEqual(payload["non_negotiables"], "never bulk-delete")

    @patch.object(ec, "resource_exists", return_value=False)
    @patch.object(ec, "get_env_config", return_value={"tag": "default"})
    @patch.object(ec, "_request", side_effect=ec.ConnectorError("Persona doctype not provisioned"))
    def test_swallows_failure_when_doctype_not_provisioned(self, mock_request, mock_get_env_config, mock_resource_exists):
        with patch("sys.stderr") as mock_stderr:
            result = ec.ensure_persona_registered(
                "default", persona_code="qkeee-erp-sales", persona_label="Sales",
            )
        self.assertIsNone(result)
        self.assertTrue(mock_stderr.write.called)


if __name__ == "__main__":
    unittest.main()
