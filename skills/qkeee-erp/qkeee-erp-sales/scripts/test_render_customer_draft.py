#!/usr/bin/env python3
import unittest

from render_customer_draft import render_customer_draft


class TestCustomerDraft(unittest.TestCase):
    def test_complete_customer_with_email_is_ready(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
            "contact_email": "buyer@acme.example.com",
        })
        self.assertTrue(result["ready"])
        self.assertEqual(result["issues"], [])
        self.assertIsNotNone(result["contact_payload"])

    def test_complete_customer_with_mobile_only_is_ready(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
            "contact_mobile": "+1-555-0100",
        })
        self.assertTrue(result["ready"])

    def test_missing_mandatory_fields_flagged(self):
        result = render_customer_draft(fields={"customer_name": "Acme Retail"})
        self.assertFalse(result["ready"])
        self.assertTrue(any("customer_type" in i for i in result["issues"]))
        self.assertTrue(any("customer_group" in i for i in result["issues"]))
        self.assertTrue(any("territory" in i for i in result["issues"]))

    def test_no_contact_channel_flagged_not_silently_dropped(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
        })
        self.assertFalse(result["ready"])
        self.assertTrue(any("primary contact channel" in i for i in result["issues"]))
        self.assertIsNone(result["contact_payload"])

    def test_low_confidence_field_flagged(self):
        result = render_customer_draft(
            fields={
                "customer_name": "Acme Retail",
                "customer_type": "Company",
                "customer_group": "Commercial",
                "territory": "United States",
                "contact_email": "buyer@acme.example.com",
            },
            confidence={"territory": 0.4},
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("Low-confidence field: territory" in i for i in result["issues"]))

    def test_execute_order_includes_contact_steps_when_contact_present(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
            "contact_email": "buyer@acme.example.com",
        })
        self.assertEqual(len(result["execute_order"]), 3)

    def test_low_confidence_optional_field_flagged(self):
        result = render_customer_draft(
            fields={
                "customer_name": "Acme Retail",
                "customer_type": "Company",
                "customer_group": "Commercial",
                "territory": "United States",
                "contact_email": "buyer@acme.example.com",
                "tax_id": "GARBLED123",
            },
            confidence={"tax_id": 0.3},
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("Low-confidence field: tax_id" in i for i in result["issues"]))

    def test_contact_link_name_is_explicit_placeholder_not_guessed(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
            "contact_email": "buyer@acme.example.com",
        })
        link_name = result["contact_payload"]["links"][0]["link_name"]
        self.assertNotEqual(link_name, "Acme Retail")
        self.assertIn("FILL IN", link_name)

    def test_execute_order_customer_only_without_contact(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
        })
        self.assertEqual(result["execute_order"], ["create Customer"])

    def test_garbage_email_flagged(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
            "contact_email": "n/a",
        })
        self.assertFalse(result["ready"])
        self.assertTrue(any("doesn't look like a real email" in i for i in result["issues"]))

    def test_valid_email_not_flagged_for_format(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
            "contact_email": "buyer@acme.example.com",
        })
        self.assertTrue(result["ready"])

    def test_garbage_mobile_flagged(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
            "contact_mobile": "unknown",
        })
        self.assertFalse(result["ready"])
        self.assertTrue(any("doesn't look like a real phone number" in i for i in result["issues"]))

    def test_valid_mobile_not_flagged_for_format(self):
        result = render_customer_draft(fields={
            "customer_name": "Acme Retail",
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "United States",
            "contact_mobile": "+1-555-0100",
        })
        self.assertTrue(result["ready"])


if __name__ == "__main__":
    unittest.main()
