#!/usr/bin/env python3
"""Regression tests for execute_write.py (F1, .scratch/hermes-erp-bot-
reliability/spec.md). Bare `import execute_write` matches this repo's
convention for a top-level scripts/ module — pytest inserts the file's
own directory into sys.path when collecting it (no __init__.py there).

Not re-testing mutate_resource()/gated_mutate_resource() themselves —
core/test_client.py covers those. These tests cover what this script adds:
every domain's allowlist actually being registered (the reason this file
exists over the bare `core/client.py mutate --domain` CLI), the loud
missing-context warnings, and the --domain-vs-gated dispatch."""

import contextlib
import io
import sys
import unittest
from unittest.mock import patch

import execute_write
from core.client import DOMAIN_WRITE_ALLOWLISTS


def _run_cli(argv):
    """Shared by DispatchTests and PurchaseSourcedItemFlagTests — runs
    execute_write._cli() against a given argv, capturing stdout/stderr
    and the effective exit code (0 if it returned normally)."""
    with patch.object(sys, "argv", ["execute_write.py"] + argv):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                execute_write._cli()
                code = 0
            except SystemExit as e:
                code = e.code
    return code, buf_out.getvalue(), buf_err.getvalue()


class DomainAllowlistsRegisteredOnImportTests(unittest.TestCase):
    """The entire reason this file exists: core/client.py's own bare
    `mutate --domain <slug>` CLI subcommand 404s standalone because no
    domains/*.py module has been imported in that process. Importing
    execute_write must register every one of them."""

    def test_every_known_domain_has_a_registered_allowlist(self):
        for domain in execute_write._KNOWN_DOMAINS:
            with self.subTest(domain=domain):
                self.assertIn(domain, DOMAIN_WRITE_ALLOWLISTS)
                self.assertTrue(DOMAIN_WRITE_ALLOWLISTS[domain] is not None)

    def test_known_domains_match_the_fixed_enum(self):
        # 00-conventions.md's fixed domain-slug enum, minus manufacturing/
        # doc-extraction (no write path — see domains/__init__.py).
        expected = {"accounts", "fixed_assets", "hr_payroll", "inventory",
                    "mis", "procurement", "sales", "system_admin"}
        self.assertEqual(execute_write._KNOWN_DOMAINS, expected)


class PreflightContextWarningTests(unittest.TestCase):
    """Loud, not blocking — see execute_write.py's module docstring. Must
    name exactly what's missing, matching the F1 failure mode (hardcoded
    SID="", no channel_metadata, no latest_prompt)."""

    class _Args:
        def __init__(self, session_id=None, channel_metadata=None, latest_prompt=None):
            self.session_id = session_id
            self.channel_metadata = channel_metadata
            self.latest_prompt = latest_prompt

    def _stderr_from(self, args) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            execute_write._preflight_context_check(args)
        return buf.getvalue()

    def test_all_missing_warns_all_three(self):
        out = self._stderr_from(self._Args())
        self.assertIn("--session-id not given", out)
        self.assertIn("--channel-metadata not given", out)
        self.assertIn("--latest-prompt not given", out)

    def test_all_present_warns_nothing(self):
        out = self._stderr_from(self._Args(
            session_id="sess-1", channel_metadata='{"space": "spaces/x"}',
            latest_prompt="create the supplier from this invoice",
        ))
        self.assertEqual(out, "")

    def test_partial_only_warns_the_missing_ones(self):
        out = self._stderr_from(self._Args(session_id="sess-1"))
        self.assertNotIn("--session-id not given", out)
        self.assertIn("--channel-metadata not given", out)
        self.assertIn("--latest-prompt not given", out)


class DispatchTests(unittest.TestCase):
    """--domain given -> that domain module's OWN mutate() (never
    core.client.mutate_resource() directly — see module docstring, F2's
    Supplier-KYC gate is why); --domain omitted -> gated_mutate_resource(),
    refusing cleanly (not a traceback) without a token."""

    _run = staticmethod(_run_cli)

    @patch.object(execute_write.sales, "mutate")
    def test_domain_given_routes_to_that_domains_own_mutate(self, mock_mutate):
        mock_mutate.return_value = {"data": {"name": "SO-0001"}, "_audit_log_status": "ok"}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Quotation", "--action", "create", "--domain", "sales",
            "--payload", '{"customer": "Acme"}',
            "--session-id", "sess-1", "--channel-metadata", '{"space": "x"}',
            "--latest-prompt", "create this quotation",
        ])
        self.assertEqual(code, 0)
        mock_mutate.assert_called_once()
        self.assertNotIn("domain", mock_mutate.call_args.kwargs)  # sales.mutate() bakes its own domain in
        self.assertIn("SO-0001", out)

    @patch.object(execute_write.procurement, "mutate")
    def test_supplier_create_forwards_kyc_flags_to_procurement_mutate(self, mock_mutate):
        mock_mutate.return_value = {"data": {"name": "Acme Supplies"},
                                     "_audit_log_status": "ok", "_kyc": {"waived": False}}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Supplier", "--action", "create", "--domain", "procurement",
            "--payload", '{"supplier_name": "Acme Supplies", "supplier_type": "Company"}',
            "--kyc", '{"address": {"address_line1": "1 Main St", "gstin": "27AAECG2483J1ZE"}}',
            "--session-id", "sess-1", "--channel-metadata", '{"space": "x"}',
            "--latest-prompt", "create this supplier",
        ])
        self.assertEqual(code, 0)
        mock_mutate.assert_called_once()
        self.assertEqual(mock_mutate.call_args.kwargs.get("kyc"), {"address": {
            "address_line1": "1 Main St", "gstin": "27AAECG2483J1ZE"}})
        self.assertEqual(mock_mutate.call_args.kwargs.get("kyc_waiver_confirmed"), False)

    def test_kyc_flag_rejected_when_not_supplier_create(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Purchase Order", "--action", "create", "--domain", "procurement",
            "--payload", '{}', "--kyc-waiver-confirmed",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("only apply to --domain procurement --doctype Supplier --action create", err)

    def test_domain_omitted_without_token_refuses_cleanly(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create",
            "--payload", '{"item_code": "X"}',
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("--confirmation-token and --issued-at are required", err)

    def test_domain_omitted_with_token_but_no_user_confirmation_text_refuses_cleanly(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create",
            "--payload", '{"item_code": "X"}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("--user-confirmation-text is required", err)

    @patch.object(execute_write, "gated_mutate_resource")
    def test_domain_omitted_with_token_and_confirmation_text_routes_to_gated_mutate(self, mock_gated):
        mock_gated.return_value = {"data": {"name": "APPLE-IPAD"}, "_audit_log_status": "ok"}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create",
            "--payload", '{"item_code": "APPLE-IPAD"}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
            "--user-confirmation-text", "yes ABC123",
            "--session-id", "sess-1", "--channel-metadata", '{"space": "x"}',
            "--latest-prompt", "create this item",
        ])
        self.assertEqual(code, 0)
        mock_gated.assert_called_once()
        self.assertEqual(mock_gated.call_args.kwargs.get("confirmation_token"), "abc123")
        self.assertEqual(mock_gated.call_args.kwargs.get("user_confirmation_text"), "yes ABC123")


class PurchaseSourcedItemFlagTests(unittest.TestCase):
    """--purchase-sourced-item (F6, F9): applies item_write_helpers.
    apply_purchase_sourced_item_defaults() to --payload before the write
    fires, only for --doctype Item --action create."""

    _run = staticmethod(_run_cli)

    def test_flag_rejected_for_non_item_create(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Purchase Order", "--action", "create", "--domain", "procurement",
            "--payload", '{}', "--purchase-sourced-item",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("--purchase-sourced-item only applies to --doctype Item --action create", err)

    @patch.object(execute_write, "gated_mutate_resource")
    def test_flag_applies_defaults_before_dispatch(self, mock_gated):
        mock_gated.return_value = {"data": {"name": "APPLE-IPAD-AIR11-128GB"}, "_audit_log_status": "ok"}
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create", "--purchase-sourced-item",
            "--payload", '{"item_code": "APPLE-IPAD-AIR11-128GB", "item_name": "iPad Air"}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
            "--user-confirmation-text", "yes ABC123",
            "--session-id", "s", "--channel-metadata", '{"space": "x"}', "--latest-prompt", "p",
        ])
        self.assertEqual(code, 0)
        mock_gated.assert_called_once()
        sent_payload = mock_gated.call_args.kwargs.get("payload")
        self.assertEqual(sent_payload["is_purchase_item"], 1)
        self.assertEqual(sent_payload["is_sales_item"], 0)
        self.assertEqual(sent_payload["item_name"], "iPad Air")

    def test_bare_standard_rate_refused_before_dispatch(self):
        code, out, err = self._run([
            "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
            "--doctype", "Item", "--action", "create", "--purchase-sourced-item",
            "--payload", '{"item_code": "X", "standard_rate": 40677.98}',
            "--confirmation-token", "abc123", "--issued-at", "1700000000",
            "--user-confirmation-text", "yes ABC123",
        ])
        self.assertNotEqual(code, 0)
        self.assertIn("Standard SELLING", err)
        self.assertIn("build_standard_buying_item_price_payload", err)

    def test_flag_omitted_leaves_payload_untouched_standard_rate_and_all(self):
        with patch.object(execute_write, "gated_mutate_resource") as mock_gated:
            mock_gated.return_value = {"data": {"name": "X"}, "_audit_log_status": "ok"}
            self._run([
                "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
                "--doctype", "Item", "--action", "create",
                "--payload", '{"item_code": "X", "standard_rate": 1.0}',
                "--confirmation-token", "abc123", "--issued-at", "1700000000",
                "--user-confirmation-text", "yes ABC123",
                "--session-id", "s", "--channel-metadata", '{"space": "x"}', "--latest-prompt", "p",
            ])
        sent_payload = mock_gated.call_args.kwargs.get("payload")
        self.assertNotIn("is_purchase_item", sent_payload)
        self.assertEqual(sent_payload["standard_rate"], 1.0)


class AuditStatusSurfacedTests(unittest.TestCase):
    @patch.object(execute_write.procurement, "mutate")
    def test_non_ok_audit_status_warns(self, mock_mutate):
        mock_mutate.return_value = {"data": {"name": "X"}, "_audit_log_status": "insert_failed"}
        with patch.object(sys, "argv", ["execute_write.py",
                "--tag", "DEMO_ERP", "--mode", "read-write", "--requested-by", "user@org.com",
                "--doctype", "Supplier", "--action", "create", "--domain", "procurement",
                "--payload", '{"supplier_name": "X", "supplier_type": "Company"}',
                "--kyc-waiver-confirmed",
                "--session-id", "s", "--channel-metadata", "{}", "--latest-prompt", "p",
        ]):
            buf_err = io.StringIO()
            with contextlib.redirect_stderr(buf_err), contextlib.redirect_stdout(io.StringIO()):
                execute_write._cli()
        self.assertIn("insert_failed", buf_err.getvalue())
        self.assertIn("NOT reliably in the audit trail", buf_err.getvalue())


if __name__ == "__main__":
    unittest.main()
