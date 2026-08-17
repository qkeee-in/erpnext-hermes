#!/usr/bin/env python3
import unittest

from render_movement_draft import render_movement_draft, RenderError


class TestRenderMovementDraft(unittest.TestCase):
    def test_unknown_purpose_raises(self):
        with self.assertRaises(RenderError):
            render_movement_draft("Teleport", "Qkeee LLP", "2026-08-10", [{"asset": "A"}], {})

    def test_empty_items_raises(self):
        with self.assertRaises(RenderError):
            render_movement_draft("Transfer", "Qkeee LLP", "2026-08-10", [], {})

    def test_item_missing_asset_raises(self):
        with self.assertRaises(RenderError):
            render_movement_draft("Transfer", "Qkeee LLP", "2026-08-10", [{}], {})

    def test_ready_when_source_matches_actual(self):
        items = [{"asset": "ACC-ASS-1", "source_location": "HQ", "target_location": "Branch"}]
        out = render_movement_draft("Transfer", "Qkeee LLP", "2026-08-10", items, {"ACC-ASS-1": "HQ"})
        self.assertIn("READY", out)

    def test_incomplete_when_source_mismatches_actual(self):
        items = [{"asset": "ACC-ASS-1", "source_location": "Branch", "target_location": "HQ"}]
        out = render_movement_draft("Transfer", "Qkeee LLP", "2026-08-10", items, {"ACC-ASS-1": "HQ"})
        self.assertIn("INCOMPLETE", out)
        self.assertIn("does not match", out)

    def test_incomplete_when_no_actual_location_provided(self):
        items = [{"asset": "ACC-ASS-1", "source_location": "HQ", "target_location": "Branch"}]
        out = render_movement_draft("Transfer", "Qkeee LLP", "2026-08-10", items, {})
        self.assertIn("INCOMPLETE", out)
        self.assertIn("cannot", out)

    def test_receipt_exempt_from_source_check(self):
        items = [{"asset": "ACC-ASS-1", "target_location": "HQ"}]
        out = render_movement_draft("Receipt", "Qkeee LLP", "2026-08-10", items, {})
        self.assertIn("READY", out)

    def test_stale_location_snapshot_flagged(self):
        items = [{"asset": "ACC-ASS-1", "source_location": "HQ", "target_location": "Branch"}]
        out = render_movement_draft(
            "Transfer", "Qkeee LLP", "2026-08-10", items, {"ACC-ASS-1": "HQ"},
            actual_current_locations_fetched_at="2026-08-10T10:00:00+00:00",
            now="2026-08-10T10:30:00+00:00",
        )
        self.assertIn("INCOMPLETE", out)
        self.assertIn("staleness limit", out)

    def test_fresh_location_snapshot_ready(self):
        items = [{"asset": "ACC-ASS-1", "source_location": "HQ", "target_location": "Branch"}]
        out = render_movement_draft(
            "Transfer", "Qkeee LLP", "2026-08-10", items, {"ACC-ASS-1": "HQ"},
            actual_current_locations_fetched_at="2026-08-10T10:00:00+00:00",
            now="2026-08-10T10:00:30+00:00",
        )
        self.assertIn("READY", out)


if __name__ == "__main__":
    unittest.main()
