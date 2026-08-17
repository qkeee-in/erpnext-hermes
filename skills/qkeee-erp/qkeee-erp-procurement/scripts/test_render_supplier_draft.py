#!/usr/bin/env python3
import unittest

from render_supplier_draft import render_supplier_draft


class TestSupplierDraft(unittest.TestCase):
    def test_complete_supplier_is_ready(self):
        result = render_supplier_draft(
            fields={
                "supplier_name": "Acme Traders",
                "supplier_group": "Hardware",
                "supplier_type": "Company",
                "country": "India",
                "tax_id": "AAAAA0000A",
                "default_bank_account": "Acme Traders - HDFC",
            },
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["problems"], [])

    def test_missing_mandatory_field_flagged(self):
        result = render_supplier_draft(fields={"supplier_name": "Acme Traders"})
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("supplier_group" in p for p in result["problems"]))
        self.assertTrue(any("supplier_type" in p for p in result["problems"]))

    def test_no_bank_details_flagged_not_silently_dropped(self):
        result = render_supplier_draft(
            fields={
                "supplier_name": "Acme Traders",
                "supplier_group": "Hardware",
                "supplier_type": "Company",
                "country": "India",
                "tax_id": "AAAAA0000A",
            },
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("bank details" in p for p in result["problems"]))

    def test_low_confidence_bank_account_flagged_even_if_present(self):
        result = render_supplier_draft(
            fields={
                "supplier_name": "Acme Traders",
                "supplier_group": "Hardware",
                "supplier_type": "Company",
                "country": "India",
                "tax_id": "AAAAA0000A",
                "default_bank_account": "Acme Traders - HDFC",
            },
            confidence={"default_bank_account": 0.5},
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("default_bank_account" in p and "low confidence" in p for p in result["problems"]))

    def test_low_confidence_field_flagged_even_if_present(self):
        result = render_supplier_draft(
            fields={
                "supplier_name": "Acme Traders",
                "supplier_group": "Hardware",
                "supplier_type": "Company",
                "country": "India",
                "tax_id": "AAAAA0000A",
                "default_bank_account": "Acme Traders - HDFC",
            },
            confidence={"tax_id": 0.4},
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("tax_id" in p and "low confidence" in p for p in result["problems"]))

    def test_never_silently_drops_a_field_from_markdown(self):
        result = render_supplier_draft(fields={"supplier_name": "Acme"})
        self.assertIn("supplier_name", result["markdown"])
        self.assertIn("supplier_group", result["markdown"])


if __name__ == "__main__":
    unittest.main()
