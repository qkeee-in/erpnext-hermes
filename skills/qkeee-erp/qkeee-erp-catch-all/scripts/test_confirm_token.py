#!/usr/bin/env python3
"""Unit tests for confirm_token.py — the token/freshness mechanics
gated_mutate_resource() relies on. Run: python scripts/test_confirm_token.py"""

import time
import unittest

from confirm_token import DEFAULT_TOKEN_TTL_SECONDS, advisory_write_token, is_fresh


class TestAdvisoryWriteToken(unittest.TestCase):
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
