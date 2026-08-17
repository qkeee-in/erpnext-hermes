#!/usr/bin/env python3
import unittest

from render_customization_draft import RenderError, render_customization_draft


class TestRenderCustomizationDraft(unittest.TestCase):
    def test_requires_reason(self):
        with self.assertRaises(RenderError):
            render_customization_draft(kind="custom_field", dt="ToDo", reason="",
                                        fieldname="x", label="X", fieldtype="Data")

    def test_bad_kind_rejected(self):
        with self.assertRaises(RenderError):
            render_customization_draft(kind="workflow_rewrite", dt="ToDo", reason="x")

    def test_custom_field_missing_fields_blocked(self):
        out = render_customization_draft(kind="custom_field", dt="ToDo", reason="need it")
        self.assertIn("BLOCKED", out)
        self.assertIn("Ready to apply:** NO", out)

    def test_custom_field_bad_fieldname_blocked(self):
        out = render_customization_draft(kind="custom_field", dt="ToDo", reason="need it",
                                          fieldname="Not Snake Case", label="X", fieldtype="Data")
        self.assertIn("BLOCKED", out)

    def test_custom_field_collision_blocked(self):
        out = render_customization_draft(kind="custom_field", dt="ToDo", reason="need it",
                                          fieldname="description", label="Description",
                                          fieldtype="Data", existing_fieldnames=["description"])
        self.assertIn("already exists", out)
        self.assertIn("Ready to apply:** NO", out)

    def test_custom_field_valid_ready(self):
        out = render_customization_draft(kind="custom_field", dt="ToDo", reason="track source",
                                          fieldname="lead_source", label="Lead Source",
                                          fieldtype="Data", existing_fieldnames=["description"])
        self.assertIn("Ready to apply:** YES", out)
        self.assertIn("re-query", out.lower())

    def test_property_setter_missing_fields_blocked(self):
        out = render_customization_draft(kind="property_setter", dt="Customer", reason="hide field")
        self.assertIn("BLOCKED", out)

    def test_property_setter_valid_ready(self):
        out = render_customization_draft(kind="property_setter", dt="Customer", reason="make optional",
                                          property="reqd", current_value=1, new_value=0)
        self.assertIn("Ready to apply:** YES", out)
        self.assertIn("reqd", out)


if __name__ == "__main__":
    unittest.main()
