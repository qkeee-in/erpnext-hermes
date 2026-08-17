#!/usr/bin/env python3
"""
Shape checks for doctype_defs.py against references/bot-doctypes-design.md.
Run: python scripts/test_doctype_defs.py

These are structural/offline checks only (field names, permission rows,
creation order) — NOT a substitute for live-validating that ERPNext's REST
API actually accepts these payloads via POST /api/resource/DocType (see
SKILL.md's outstanding live-validation item, and the design doc's note
that a Link field naming a not-yet-existing DocType is assumed, not yet
confirmed, to be accepted at DocType-create time).
"""

import unittest

from doctype_defs import (
    ALL_DOCTYPES, DEFERRED_FIELD_PATCHES, PERSONA, SESSION, MESSAGE, AUDIT_LOG, ROLE_NAME, ROLE_PAYLOAD,
)


class TestRolePayload(unittest.TestCase):
    def test_role_shape(self):
        self.assertEqual(ROLE_PAYLOAD["doctype"], "Role")
        self.assertEqual(ROLE_PAYLOAD["role_name"], ROLE_NAME)
        self.assertEqual(ROLE_PAYLOAD["role_name"], "Qkeee Bot")
        self.assertEqual(ROLE_PAYLOAD["desk_access"], 1)


class TestAllDoctypesPresent(unittest.TestCase):
    def test_exactly_four_doctypes_in_design_order(self):
        self.assertEqual(
            [d["name"] for d in ALL_DOCTYPES],
            ["Qkeee Bot Persona", "Qkeee Bot Session", "Qkeee Bot Message", "Qkeee Bot Audit Log"],
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


class TestSession(unittest.TestCase):
    def test_qkeee_bot_role_can_create_and_write_but_not_delete(self):
        perm = next(p for p in SESSION["permissions"] if p["role"] == ROLE_NAME)
        self.assertEqual(perm["create"], 1)
        self.assertEqual(perm["write"], 1)
        self.assertEqual(perm["delete"], 0)

    def test_user_and_persona_are_required_links(self):
        by_name = {f["fieldname"]: f for f in SESSION["fields"]}
        self.assertEqual(by_name["user"]["fieldtype"], "Link")
        self.assertEqual(by_name["user"]["options"], "User")
        self.assertEqual(by_name["user"]["reqd"], 1)
        self.assertEqual(by_name["persona"]["options"], "Qkeee Bot Persona")


class TestMessage(unittest.TestCase):
    def test_qkeee_bot_role_is_create_only_not_write(self):
        """Design doc decision 9: Message rows are immutable after insert."""
        perm = next(p for p in MESSAGE["permissions"] if p["role"] == ROLE_NAME)
        self.assertEqual(perm["create"], 1)
        self.assertEqual(perm["write"], 0)

    def test_links_to_session(self):
        by_name = {f["fieldname"]: f for f in MESSAGE["fields"]}
        self.assertEqual(by_name["session"]["options"], "Qkeee Bot Session")

    def test_linked_audit_log_is_not_in_the_create_payload(self):
        """Live-confirmed against demo.qkeee.in: Frappe rejects a
        Link field naming a not-yet-existing DocType at DocType-create time
        (WrongOptionsDoctypeLinkError). Message is created before Audit Log
        in ALL_DOCTYPES, so linked_audit_log must NOT be part of this
        create payload — it's patched in afterward, see
        TestDeferredFieldPatches below."""
        fieldnames = {f["fieldname"] for f in MESSAGE["fields"]}
        self.assertNotIn("linked_audit_log", fieldnames)


class TestDeferredFieldPatches(unittest.TestCase):
    def test_linked_audit_log_is_deferred_until_audit_log_exists(self):
        patch = next(p for p in DEFERRED_FIELD_PATCHES if p["doctype"] == "Qkeee Bot Message")
        self.assertEqual(patch["field"]["fieldname"], "linked_audit_log")
        self.assertEqual(patch["field"]["options"], "Qkeee Bot Audit Log")
        self.assertEqual(patch["requires"], "Qkeee Bot Audit Log")


class TestAuditLog(unittest.TestCase):
    def test_is_submittable(self):
        self.assertEqual(AUDIT_LOG["is_submittable"], 1)

    def test_session_is_data_not_link(self):
        """Design doc decision 10: session is a raw Data string, not a Link
        to Qkeee Bot Session, since Session may not exist outside debug mode."""
        by_name = {f["fieldname"]: f for f in AUDIT_LOG["fields"]}
        self.assertEqual(by_name["session"]["fieldtype"], "Data")

    def test_qkeee_bot_role_can_submit_but_not_delete(self):
        perm = next(p for p in AUDIT_LOG["permissions"] if p["role"] == ROLE_NAME)
        self.assertEqual(perm["submit"], 1)
        self.assertEqual(perm["delete"], 0)

    def test_user_approved_defaults_to_not_required(self):
        by_name = {f["fieldname"]: f for f in AUDIT_LOG["fields"]}
        self.assertEqual(by_name["user_approved"]["default"], "Not Required")


class TestNoRoleGetsDelete(unittest.TestCase):
    def test_no_permission_row_grants_delete_on_any_doctype(self):
        """Permission matrix in the design doc: 'No role gets delete on any
        of the four doctypes' — append-only audit trail by default."""
        for d in ALL_DOCTYPES:
            for perm in d["permissions"]:
                self.assertEqual(perm["delete"], 0, f"{d['name']} / {perm['role']}")


if __name__ == "__main__":
    unittest.main()
