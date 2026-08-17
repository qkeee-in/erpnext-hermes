#!/usr/bin/env python3
import unittest

from render_employee_draft import render_employee_draft


class TestEmployeeDraft(unittest.TestCase):
    def _complete_fields(self):
        return {
            "first_name": "Jane",
            "last_name": "Doe",
            "gender": "Female",
            "date_of_birth": "1995-01-01",
            "date_of_joining": "2026-08-10",
            "company": "Qkeee LLP",
        }

    def test_complete_employee_is_ready(self):
        result = render_employee_draft(fields=self._complete_fields())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["problems"], [])

    def test_missing_mandatory_field_flagged(self):
        result = render_employee_draft(fields={"first_name": "Jane"})
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("gender" in p for p in result["problems"]))
        self.assertTrue(any("date_of_birth" in p for p in result["problems"]))
        self.assertTrue(any("company" in p for p in result["problems"]))

    def test_left_status_requires_relieving_date(self):
        fields = self._complete_fields()
        fields["status"] = "Left"
        result = render_employee_draft(fields=fields)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("relieving_date" in p for p in result["problems"]))

    def test_left_status_with_relieving_date_ready(self):
        fields = self._complete_fields()
        fields["status"] = "Left"
        fields["relieving_date"] = "2026-09-01"
        result = render_employee_draft(fields=fields)
        self.assertEqual(result["status"], "ready")

    def test_pii_fields_flagged_prominently(self):
        fields = self._complete_fields()
        fields["bank_ac_no"] = "1234567890"
        fields["passport_number"] = "A1234567"
        result = render_employee_draft(fields=fields)
        self.assertIn("bank_ac_no", result["pii_fields_present"])
        self.assertIn("passport_number", result["pii_fields_present"])
        self.assertIn("CONTAINS SENSITIVE PII", result["markdown"])

    def test_compensation_and_address_flagged_as_pii(self):
        # Regression: adversarial review found `ctc` (the actual
        # compensation figure) and home address/personal phone fields were
        # missing from PII_SENSITIVE_FIELDS despite being named categories
        # in SKILL.md's non-negotiable.
        fields = self._complete_fields()
        fields["ctc"] = 1200000
        fields["current_address"] = "123 Main St"
        fields["cell_number"] = "+1-555-0100"
        result = render_employee_draft(fields=fields)
        self.assertIn("ctc", result["pii_fields_present"])
        self.assertIn("current_address", result["pii_fields_present"])
        self.assertIn("cell_number", result["pii_fields_present"])

    def test_no_pii_no_warning(self):
        result = render_employee_draft(fields=self._complete_fields())
        self.assertEqual(result["pii_fields_present"], [])
        self.assertNotIn("CONTAINS SENSITIVE PII", result["markdown"])

    def test_low_confidence_field_flagged(self):
        fields = self._complete_fields()
        result = render_employee_draft(fields=fields, confidence={"date_of_birth": 0.3})
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("date_of_birth" in p and "low confidence" in p for p in result["problems"]))

    def test_is_update_changes_heading_not_gate(self):
        result = render_employee_draft(fields=self._complete_fields(), is_update=True)
        self.assertIn("Employee update", result["markdown"])
        self.assertEqual(result["status"], "ready")


if __name__ == "__main__":
    unittest.main()
