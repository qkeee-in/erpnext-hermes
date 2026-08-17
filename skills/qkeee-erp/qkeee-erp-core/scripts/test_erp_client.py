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


if __name__ == "__main__":
    unittest.main()
