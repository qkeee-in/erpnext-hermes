#!/usr/bin/env python3
import unittest

from render_asset_draft import render_asset_draft, RenderError

BASE = {
    "asset_name": "Laptop #1",
    "item_code": "LAPTOP-01",
    "company": "Qkeee LLP",
    "location": "Head Office",
    "purchase_date": "2026-01-01",
    "gross_purchase_amount": 1200,
    "is_existing_asset": 1,
    "asset_category": "Office Equipment",
}


class TestRenderAssetDraft(unittest.TestCase):
    def test_missing_required_raises(self):
        with self.assertRaises(RenderError):
            render_asset_draft({"asset_name": "Laptop"})

    def test_ready_when_complete_no_depreciation(self):
        out = render_asset_draft(dict(BASE))
        self.assertIn("READY", out)
        self.assertNotIn("INCOMPLETE", out)

    def test_incomplete_when_no_source(self):
        asset = dict(BASE)
        del asset["is_existing_asset"]
        out = render_asset_draft(asset)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("ambiguous", out)

    def test_incomplete_when_zero_amount(self):
        asset = dict(BASE)
        asset["gross_purchase_amount"] = 0
        out = render_asset_draft(asset)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("gross_purchase_amount", out)

    def test_incomplete_when_negative_amount(self):
        asset = dict(BASE)
        asset["gross_purchase_amount"] = -5000
        out = render_asset_draft(asset)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("negative", out)

    def test_incomplete_without_asset_category(self):
        asset = dict(BASE)
        del asset["asset_category"]
        out = render_asset_draft(asset)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("asset_category", out)

    def test_depreciation_requires_finance_book(self):
        asset = dict(BASE)
        asset["calculate_depreciation"] = 1
        out = render_asset_draft(asset)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("no finance_books entry", out)

    def test_depreciation_ready_with_complete_finance_book(self):
        asset = dict(BASE)
        asset["calculate_depreciation"] = 1
        asset["finance_books"] = [{
            "depreciation_method": "Straight Line",
            "total_number_of_depreciations": 12,
            "frequency_of_depreciation": 1,
            "depreciation_start_date": "2026-02-28",
        }]
        out = render_asset_draft(asset)
        self.assertIn("READY", out)

    def test_depreciation_incomplete_finance_book_missing_key(self):
        asset = dict(BASE)
        asset["calculate_depreciation"] = 1
        asset["finance_books"] = [{"depreciation_method": "Straight Line"}]
        out = render_asset_draft(asset)
        self.assertIn("INCOMPLETE", out)
        self.assertIn("finance_books[0] missing", out)

    def test_source_from_purchase_receipt_satisfies_gap(self):
        asset = dict(BASE)
        del asset["is_existing_asset"]
        asset["purchase_receipt"] = "PR-0001"
        out = render_asset_draft(asset)
        self.assertIn("READY", out)
        self.assertIn("Purchase Receipt PR-0001", out)


if __name__ == "__main__":
    unittest.main()
