#!/usr/bin/env python3
"""Unit tests for confirm_token.py's shared primitives — compute_token()
and is_fresh() — plus this skill's own advisory_write_token() constructor
(merged in from the former qkeee-erp-catch-all skill, 2026-08-18), the
token/freshness mechanics gated_mutate_resource() relies on.
Run: python scripts/test_confirm_token.py

Other capability-specific token constructors (depreciation_run_token(),
permission_change_token(), etc.) live in each persona skill's own
confirm_token.py and are tested there, on top of these two primitives."""

import time
import unittest

from confirm_token import DEFAULT_TOKEN_TTL_SECONDS, advisory_write_token, compute_token, is_fresh


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


class TestAdvisoryWriteToken(unittest.TestCase):
    """This skill's own token constructor (see confirm_token.py's module
    docstring) — gated_mutate_resource() in erp_client.py refuses every
    write without a matching, fresh token from this function."""

    def test_deterministic_for_same_facts(self):
        t1 = advisory_write_token("create", "CRM Lead", None, {"a": 1}, "priya@org.com", 1000)
        t2 = advisory_write_token("create", "CRM Lead", None, {"a": 1}, "priya@org.com", 1000)
        self.assertEqual(t1, t2)

    def test_changes_if_payload_changes(self):
        t1 = advisory_write_token("create", "CRM Lead", None, {"a": 1}, "priya@org.com", 1000)
        t2 = advisory_write_token("create", "CRM Lead", None, {"a": 2}, "priya@org.com", 1000)
        self.assertNotEqual(t1, t2)

    def test_changes_if_doctype_changes(self):
        t1 = advisory_write_token("create", "CRM Lead", None, {"a": 1}, "priya@org.com", 1000)
        t2 = advisory_write_token("create", "Helpdesk Ticket", None, {"a": 1}, "priya@org.com", 1000)
        self.assertNotEqual(t1, t2)

    def test_changes_if_requested_by_changes(self):
        t1 = advisory_write_token("create", "CRM Lead", None, {"a": 1}, "priya@org.com", 1000)
        t2 = advisory_write_token("create", "CRM Lead", None, {"a": 1}, "amit@org.com", 1000)
        self.assertNotEqual(t1, t2)

    def test_requires_issued_at(self):
        with self.assertRaises(ValueError):
            advisory_write_token("create", "CRM Lead", None, {"a": 1}, "priya@org.com", None)


if __name__ == "__main__":
    unittest.main()
