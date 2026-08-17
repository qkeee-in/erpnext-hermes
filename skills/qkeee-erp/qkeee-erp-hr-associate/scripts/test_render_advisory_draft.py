#!/usr/bin/env python3
import unittest

from render_advisory_draft import RenderError, render_advisory_draft


class TestAdvisoryDraft(unittest.TestCase):
    def test_job_offer_always_advisory(self):
        result = render_advisory_draft(
            doctype="Job Offer",
            fields={"job_applicant": "HR-JA-001", "designation": "Engineer"},
            reason="compensation terms pending Finance sign-off",
        )
        self.assertEqual(result["recommended_action"], "advisory-only")
        self.assertIn("never auto-commits", result["markdown"])

    def test_employee_onboarding_always_advisory(self):
        result = render_advisory_draft(
            doctype="Employee Onboarding",
            fields={"job_applicant": "HR-JA-001"},
            reason="background check not yet returned",
        )
        self.assertEqual(result["recommended_action"], "advisory-only")

    def test_rejects_other_doctypes(self):
        with self.assertRaises(RenderError):
            render_advisory_draft(doctype="Employee", fields={}, reason="x")

    def test_numeric_string_compensation_formatted(self):
        # Regression: 2026-08-10 adversarial review found a string-typed
        # compensation figure (e.g. from doc-extraction) skipped _fmt()
        # entirely and rendered unformatted.
        result = render_advisory_draft(
            doctype="Job Offer",
            fields={"annual_ctc": "1200000"},
            reason="x",
        )
        self.assertIn("1,200,000", result["markdown"])

    def test_no_ready_status_exists(self):
        # There is no parameter combination that produces anything other
        # than "advisory-only" — this is the structural point of the
        # module. Assert the return shape has no other status key at all.
        result = render_advisory_draft(doctype="Job Offer", fields={}, reason="x")
        self.assertNotIn("status", result)
        self.assertEqual(result["recommended_action"], "advisory-only")


if __name__ == "__main__":
    unittest.main()
