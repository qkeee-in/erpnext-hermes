#!/usr/bin/env python3
import unittest

from render_quotation_draft import render_quotation_draft

HEADER = {
    "quotation_to": "Customer",
    "party_name": "Acme Retail",
    "transaction_date": "2026-08-11",
    "company": "Enfasco Inc.",
    "currency": "USD",
    "conversion_rate": 1,
    "selling_price_list": "Standard Selling",
    "price_list_currency": "USD",
    "plc_conversion_rate": 1,
    "order_type": "Sales",
}


class TestQuotationDraft(unittest.TestCase):
    def test_complete_quotation_is_ready(self):
        result = render_quotation_draft(
            fields=HEADER,
            items=[{"item_code": "SKU-1", "qty": 3, "rate": 15}],
            sales_items={"SKU-1"},
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["recommended_action"], "create-as-draft-only")

    def test_missing_party_name_flagged(self):
        fields = dict(HEADER)
        fields.pop("party_name")
        result = render_quotation_draft(fields=fields, items=[{"item_code": "SKU-1", "qty": 1}], sales_items={"SKU-1"})
        self.assertFalse(result["ready"])
        self.assertTrue(any("party_name" in i for i in result["issues"]))

    def test_missing_order_type_flagged(self):
        fields = dict(HEADER)
        fields.pop("order_type")
        result = render_quotation_draft(fields=fields, items=[{"item_code": "SKU-1", "qty": 1}], sales_items={"SKU-1"})
        self.assertFalse(result["ready"])
        self.assertTrue(any("order_type" in i for i in result["issues"]))

    def test_missing_conversion_rate_flagged(self):
        fields = dict(HEADER)
        fields.pop("conversion_rate")
        result = render_quotation_draft(fields=fields, items=[{"item_code": "SKU-1", "qty": 1}], sales_items={"SKU-1"})
        self.assertFalse(result["ready"])
        self.assertTrue(any("conversion_rate" in i for i in result["issues"]))

    def test_item_name_only_line_refused(self):
        result = render_quotation_draft(fields=HEADER, items=[{"item_name": "Widget", "qty": 1}], sales_items=set())
        self.assertFalse(result["ready"])
        self.assertTrue(any("missing item_code" in i for i in result["issues"]))

    def test_non_sales_item_refused(self):
        result = render_quotation_draft(
            fields=HEADER, items=[{"item_code": "RAW-1", "qty": 1}], sales_items={"SKU-1"},
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("not marked as a Sales Item" in i for i in result["issues"]))

    def test_unresolved_sales_items_flagged_not_assumed(self):
        result = render_quotation_draft(fields=HEADER, items=[{"item_code": "SKU-1", "qty": 1}], sales_items=None)
        self.assertFalse(result["ready"])
        self.assertTrue(any("sales-item status not confirmed" in i for i in result["issues"]))

    def test_no_items_flagged(self):
        result = render_quotation_draft(fields=HEADER, items=[], sales_items=set())
        self.assertFalse(result["ready"])
        self.assertTrue(any("No line items" in i for i in result["issues"]))

    def test_negative_qty_refused(self):
        result = render_quotation_draft(
            fields=HEADER, items=[{"item_code": "SKU-1", "qty": -3, "rate": 15}], sales_items={"SKU-1"},
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("must be greater than zero" in i for i in result["issues"]))

    def test_negative_rate_refused(self):
        result = render_quotation_draft(
            fields=HEADER, items=[{"item_code": "SKU-1", "qty": 3, "rate": -15}], sales_items={"SKU-1"},
        )
        self.assertFalse(result["ready"])
        self.assertTrue(any("cannot be negative" in i for i in result["issues"]))

    def test_zero_rate_allowed(self):
        # rate: 0 is a valid "let ERPNext price from selling_price_list"
        # signal in some flows; only negative rate is refused.
        result = render_quotation_draft(
            fields=HEADER, items=[{"item_code": "SKU-1", "qty": 3, "rate": 0}], sales_items={"SKU-1"},
        )
        self.assertTrue(result["ready"])

    def test_recommended_action_never_submit(self):
        # Even a fully-ready draft never recommends submit — that's a
        # separate, explicitly-confirmed step per the non-negotiable.
        result = render_quotation_draft(
            fields=HEADER, items=[{"item_code": "SKU-1", "qty": 3, "rate": 15}], sales_items={"SKU-1"},
        )
        self.assertEqual(result["recommended_action"], "create-as-draft-only")


if __name__ == "__main__":
    unittest.main()
