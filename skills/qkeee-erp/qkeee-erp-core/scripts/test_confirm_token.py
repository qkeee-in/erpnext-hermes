#!/usr/bin/env python3
"""Unit tests for confirm_token.py's shared primitives — compute_token()
and is_fresh(). Run: python scripts/test_confirm_token.py

Capability-specific token constructors (depreciation_run_token(),
permission_change_token(), etc.) live in each persona skill's own
confirm_token.py and are tested there, on top of these two primitives."""

import time
import unittest

from confirm_token import DEFAULT_TOKEN_TTL_SECONDS, compute_token, is_fresh


class TestComputeToken(unittest.TestCase):
    def test_deterministic_for_same_facts(self):
        t1 = compute_token(kind="x", asset="A-1", amount=100.0)
        t2 = compute_token(kind="x", asset="A-1", amount=100.0)
        self.assertEqual(t1, t2)

    def test_changes_if_any_field_changes(self):
        base = compute_token(kind="x", asset="A-1", amount=100.0)
        self.assertNotEqual(base, compute_token(kind="x", asset="A-2", amount=100.0))
        self.assertNotEqual(base, compute_token(kind="x", asset="A-1", amount=100.01))
        self.assertNotEqual(base, compute_token(kind="y", asset="A-1", amount=100.0))

    def test_field_order_does_not_matter(self):
        t1 = compute_token(kind="x", asset="A-1", amount=100.0)
        t2 = compute_token(amount=100.0, asset="A-1", kind="x")
        self.assertEqual(t1, t2)


class TestIsFresh(unittest.TestCase):
    def test_fresh_within_ttl(self):
        now = 10_000
        self.assertTrue(is_fresh(now - 100, now=now))

    def test_stale_beyond_ttl(self):
        now = 10_000
        self.assertFalse(is_fresh(now - DEFAULT_TOKEN_TTL_SECONDS - 1, now=now))

    def test_implausible_future_rejected(self):
        now = 10_000
        self.assertFalse(is_fresh(now + 1000, now=now))

    def test_small_clock_skew_tolerated(self):
        now = 10_000
        self.assertTrue(is_fresh(now + 10, now=now))

    def test_defaults_to_current_time_when_now_omitted(self):
        self.assertTrue(is_fresh(int(time.time())))


if __name__ == "__main__":
    unittest.main()
