#!/usr/bin/env python3
"""
Shape checks for doctype_defs.py against references/bot-doctypes-design.md.
Run: python scripts/test_doctype_defs.py

These are structural/offline checks only (field names, permission rows,
creation order) — NOT a substitute for live-validating that ERPNext's REST
API actually accepts these payloads via POST /api/resource/DocType.
"""

import unittest

from doctype_defs import ALL_DOCTYPES, PERSONA, AUDIT_LOG, ROLE_NAME, ROLE_PAYLOAD


class TestRolePayload(unittest.TestCase):
    def test_role_shape(self):
        self.assertEqual(ROLE_PAYLOAD["doctype"], "Role")
        self.assertEqual(ROLE_PAYLOAD["role_name"], ROLE_NAME)
        self.assertEqual(ROLE_PAYLOAD["role_name"], "Qkeee Bot")
        self.assertEqual(ROLE_PAYLOAD["desk_access"], 1)


class TestAllDoctypesPresent(unittest.TestCase):
    def test_exactly_two_doctypes(self):
        self.assertEqual(
            [d["name"] for d in ALL_DOCTYPES],
            ["Qkeee Bot Persona", "Qkeee Bot Audit Log"],
        )

    def test_every_doctype_is_custom_no_app(self):
        for d in ALL_DOCTYPES:
            self.assertEqual(d["module"], "Custom", d["name"])
            self.assertEqual(d["custom"], 1, d["name"])

    def test_every_doctype_has_a_role_permission_row(self):
        for d in ALL_DOCTYPES:
            roles = {p["role"] for p in d["permissions"]}
            self.assertIn(ROLE_NAME, roles, d["name"])
            self.assertIn("System Manager", roles, d["name"])


class TestPersona(unittest.TestCase):
    def test_autoname_by_persona_code(self):
        self.assertEqual(PERSONA["autoname"], "field:persona_code")
        fieldnames = {f["fieldname"] for f in PERSONA["fields"]}
        self.assertIn("persona_code", fieldnames)

    def test_qkeee_bot_role_is_read_only(self):
        perm = next(p for p in PERSONA["permissions"] if p["role"] == ROLE_NAME)
        self.assertEqual(perm["read"], 1)
        self.assertEqual(perm["write"], 0)
        self.assertEqual(perm["create"], 0)


class TestAuditLog(unittest.TestCase):
    def test_is_submittable(self):
        self.assertEqual(AUDIT_LOG["is_submittable"], 1)

    def test_session_is_data_not_link(self):
        """Design doc decision 10: session is a raw Data string, not a
        Link — a plain correlator, not tied to any doctype."""
        by_name = {f["fieldname"]: f for f in AUDIT_LOG["fields"]}
        self.assertEqual(by_name["session"]["fieldtype"], "Data")

    def test_no_triggering_message_field(self):
        """Audit Log has no Link field referencing a conversation-turn
        record — nothing in the schema produces one to link to."""
        fieldnames = {f["fieldname"] for f in AUDIT_LOG["fields"]}
        self.assertNotIn("triggering_message", fieldnames)

    def test_qkeee_bot_role_can_submit_but_not_delete(self):
        perm = next(p for p in AUDIT_LOG["permissions"] if p["role"] == ROLE_NAME)
        self.assertEqual(perm["submit"], 1)
        self.assertEqual(perm["delete"], 0)

    def test_user_approved_defaults_to_not_required(self):
        by_name = {f["fieldname"]: f for f in AUDIT_LOG["fields"]}
        self.assertEqual(by_name["user_approved"]["default"], "Not Required")


class TestNoRoleGetsDelete(unittest.TestCase):
    def test_no_permission_row_grants_delete_on_any_doctype(self):
        """Permission matrix in the design doc: 'No role gets delete on
        either doctype' — append-only audit trail by default."""
        for d in ALL_DOCTYPES:
            for perm in d["permissions"]:
                self.assertEqual(perm["delete"], 0, f"{d['name']} / {perm['role']}")


if __name__ == "__main__":
    unittest.main()
