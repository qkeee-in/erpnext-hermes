#!/usr/bin/env python3
"""Unit tests for render_staged_report.py. Run: python scripts/test_render_staged_report.py"""

import unittest

from render_staged_report import RenderError, render_staged_report


def _field(field, value, confidence="high", source=None, row=None):
    f = {"field": field, "value": value, "confidence": confidence}
    if source is not None:
        f["source"] = source
    if row is not None:
        f["row"] = row
    return f


class TestRequiredKeys(unittest.TestCase):
    def test_refuses_missing_value_key(self):
        with self.assertRaises(RenderError):
            render_staged_report("Sales Invoice", "inv.pdf",
                                  [{"field": "customer", "confidence": "high"}])

    def test_refuses_missing_confidence_key(self):
        with self.assertRaises(RenderError):
            render_staged_report("Sales Invoice", "inv.pdf",
                                  [{"field": "customer", "value": "Acme"}])

    def test_refuses_invalid_confidence(self):
        with self.assertRaises(RenderError):
            render_staged_report("Sales Invoice", "inv.pdf",
                                  [_field("customer", "Acme", confidence="certain")])

    def test_accepts_empty_field_list(self):
        out = render_staged_report("Sales Invoice", "inv.pdf", [])
        self.assertIn("Sales Invoice", out)
        self.assertIn("NOT WRITTEN", out)


class TestValueSemantics(unittest.TestCase):
    """None (not found) and "" (found but blank) must render distinctly,
    and must not collide via a truthy `or` check — that's the exact bug
    _display_value() exists to prevent for 0/False-shaped values too."""

    def test_none_value_renders_not_found(self):
        out = render_staged_report("Sales Invoice", "inv.pdf", [_field("customer", None)])
        self.assertIn("*(not found)*", out)

    def test_blank_string_renders_blank_not_not_found(self):
        out = render_staged_report("Employee", "form.pdf", [_field("middle_name", "")])
        self.assertIn("*(blank)*", out)
        self.assertNotIn("not found", out)

    def test_falsy_zero_value_not_misrendered_as_not_found(self):
        out = render_staged_report("Stock Entry", "form.pdf", [_field("qty", 0)])
        self.assertIn("| `qty` | 0 |", out)

    def test_falsy_false_value_not_misrendered_as_not_found(self):
        out = render_staged_report("Task", "form.pdf", [_field("is_billable", False)])
        self.assertIn("| `is_billable` | False |", out)


class TestNeedsAttention(unittest.TestCase):
    def test_not_found_field_flagged(self):
        out = render_staged_report("Sales Invoice", "inv.pdf", [_field("customer", None)])
        self.assertIn("1 field(s) need attention", out)
        self.assertIn("`customer`", out.split("need attention")[1])

    def test_low_confidence_field_flagged(self):
        out = render_staged_report("Sales Invoice", "inv.pdf",
                                    [_field("total", 100, confidence="low")])
        self.assertIn("1 field(s) need attention", out)

    def test_blank_high_confidence_field_not_flagged(self):
        out = render_staged_report("Employee", "form.pdf", [_field("middle_name", "")])
        self.assertNotIn("need attention", out)

    def test_medium_confidence_present_value_not_flagged(self):
        out = render_staged_report("Sales Invoice", "inv.pdf",
                                    [_field("total", 100, confidence="medium")])
        self.assertNotIn("need attention", out)


class TestRowGrouping(unittest.TestCase):
    def test_header_and_row_fields_rendered_separately(self):
        fields = [
            _field("customer", "Acme"),
            _field("item_code", "WIDGET-1", row="items[0]"),
            _field("qty", 5, row="items[0]"),
        ]
        out = render_staged_report("Sales Invoice", "inv.pdf", fields)
        self.assertIn("| `customer` | Acme |", out)
        self.assertIn("Line item: `items[0]`", out)
        self.assertIn("| `item_code` | WIDGET-1 |", out)

    def test_multiple_rows_kept_in_first_seen_order(self):
        fields = [
            _field("item_code", "B", row="items[1]"),
            _field("item_code", "A", row="items[0]"),
        ]
        out = render_staged_report("Sales Invoice", "inv.pdf", fields)
        self.assertLess(out.index("items[1]"), out.index("items[0]"))

    def test_source_defaults_to_dash_when_absent(self):
        out = render_staged_report("Sales Invoice", "inv.pdf", [_field("customer", "Acme")])
        self.assertIn("| `customer` | Acme | high | - |", out)


class TestNotes(unittest.TestCase):
    def test_notes_section_included_when_present(self):
        out = render_staged_report("Sales Invoice", "inv.pdf", [], notes="Handwritten total unclear.")
        self.assertIn("## Notes", out)
        self.assertIn("Handwritten total unclear.", out)

    def test_notes_section_omitted_when_absent(self):
        out = render_staged_report("Sales Invoice", "inv.pdf", [])
        self.assertNotIn("## Notes", out)


class TestReportFraming(unittest.TestCase):
    def test_status_line_never_claims_written(self):
        out = render_staged_report("Sales Invoice", "inv.pdf", [_field("customer", "Acme")])
        self.assertIn("NOT WRITTEN - review and confirm before any ERPNext create/update.", out)

    def test_footer_disclaims_erpnext_record(self):
        out = render_staged_report("Sales Invoice", "inv.pdf", [])
        self.assertIn("not an ERPNext record", out)


if __name__ == "__main__":
    unittest.main()
