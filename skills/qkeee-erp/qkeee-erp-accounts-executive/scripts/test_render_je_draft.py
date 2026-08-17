#!/usr/bin/env python3
"""Unit tests for render_je_draft.py. Run: python scripts/test_render_je_draft.py"""

import unittest

from render_je_draft import RenderError, render_je_draft


class TestJeDraftGate(unittest.TestCase):
    def test_refuses_empty_rows(self):
        with self.assertRaises(RenderError):
            render_je_draft("Acme", "2026-08-10", "test", [])

    def test_refuses_single_row(self):
        rows = [{"account": "Cash", "debit": 100, "credit": 0}]
        with self.assertRaisesRegex(RenderError, "at least two rows"):
            render_je_draft("Acme", "2026-08-10", "test", rows)

    def test_refuses_unbalanced(self):
        rows = [
            {"account": "Cash", "debit": 100, "credit": 0},
            {"account": "Expense", "debit": 0, "credit": 90},
        ]
        with self.assertRaises(RenderError):
            render_je_draft("Acme", "2026-08-10", "test", rows)

    def test_refuses_row_with_both_debit_and_credit(self):
        rows = [{"account": "Cash", "debit": 10, "credit": 10}]
        with self.assertRaises(RenderError):
            render_je_draft("Acme", "2026-08-10", "test", rows)

    def test_refuses_row_with_neither(self):
        rows = [{"account": "Cash", "debit": 0, "credit": 0}]
        with self.assertRaises(RenderError):
            render_je_draft("Acme", "2026-08-10", "test", rows)

    def test_accepts_balanced_draft(self):
        rows = [
            {"account": "Cash - QL", "debit": 100, "credit": 0},
            {"account": "Administrative Expenses - QL", "debit": 0, "credit": 100},
        ]
        out = render_je_draft("Qkeee LLP", "2026-08-10", "office supplies", rows)
        self.assertIn("DRAFT, NOT SUBMITTED", out)
        self.assertIn("ties out", out)
        self.assertIn("Advisory-first", out)

    def test_floating_point_tolerance(self):
        rows = [
            {"account": "A", "debit": 0.1, "credit": 0},
            {"account": "B", "debit": 0.2, "credit": 0},
            {"account": "C", "debit": 0, "credit": 0.3},
        ]
        out = render_je_draft("Acme", "2026-08-10", "test", rows)
        self.assertIn("ties out", out)


if __name__ == "__main__":
    unittest.main()
