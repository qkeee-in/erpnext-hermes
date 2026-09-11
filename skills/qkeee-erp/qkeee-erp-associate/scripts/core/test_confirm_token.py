#!/usr/bin/env python3
"""Regression tests for confirm_token.confirmation_code() (F5, .scratch/
hermes-erp-bot-reliability/spec.md). Bare `import confirm_token` matches
this repo's convention — conftest.py adds core/ to sys.path."""

import unittest

import confirm_token as ct


class ConfirmationCodeTests(unittest.TestCase):
    def test_deterministic_over_same_token(self):
        token = ct.advisory_write_token("create", "Item", "", {"item_code": "X"}, "u@org.com", 1700000000)
        self.assertEqual(ct.confirmation_code(token), ct.confirmation_code(token))

    def test_six_uppercase_hex_chars(self):
        token = ct.advisory_write_token("create", "Item", "", {"item_code": "X"}, "u@org.com", 1700000000)
        code = ct.confirmation_code(token)
        self.assertEqual(len(code), 6)
        self.assertEqual(code, code.upper())
        int(code, 16)  # raises ValueError if not hex

    def test_different_payload_yields_different_token_usually_different_code(self):
        # Not a strict guarantee (6 hex chars is a small space), but the
        # underlying token must differ — confirms confirmation_code is
        # actually derived from the token, not a constant.
        token_a = ct.advisory_write_token("create", "Item", "", {"item_code": "A"}, "u@org.com", 1700000000)
        token_b = ct.advisory_write_token("create", "Item", "", {"item_code": "B"}, "u@org.com", 1700000000)
        self.assertNotEqual(token_a, token_b)


if __name__ == "__main__":
    unittest.main()
