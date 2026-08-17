"""
Create-payloads for the 4 Qkeee Bot audit-trail doctypes + the Qkeee Bot
role. Synced from qkeee-erp-bot-init/references/bot-doctypes-design.md —
that file is the design source of truth; if you're changing field shapes,
update the design doc first, then this file to match.

All 4 doctypes are created as custom (custom=1) records attached to
Frappe's built-in "Custom" module — no app, no module folder, no Python
controller. See the design doc's "Why 4 doctypes, no app" section.
"""

ROLE_NAME = "Qkeee Bot"

ROLE_PAYLOAD = {
    "doctype": "Role",
    "role_name": ROLE_NAME,
    "desk_access": 1,
}


def _perm(role, **flags):
    base = {"role": role, "read": 0, "write": 0, "create": 0,
            "submit": 0, "cancel": 0, "delete": 0}
    base.update(flags)
    return base


PERSONA = {
    "doctype": "DocType",
    "name": "Qkeee Bot Persona",
    "module": "Custom",
    "custom": 1,
    "naming_rule": "By fieldname",
    "autoname": "field:persona_code",
    "track_changes": 0,
    "fields": [
        {"fieldname": "persona_code", "label": "Persona Code", "fieldtype": "Data",
         "reqd": 1, "unique": 1, "in_list_view": 1},
        {"fieldname": "persona_label", "label": "Persona Label", "fieldtype": "Data",
         "reqd": 1, "in_list_view": 1},
        {"fieldname": "default_mode", "label": "Default Mode", "fieldtype": "Select",
         "options": "Read Only\nRead Write", "default": "Read Only"},
        {"fieldname": "non_negotiables", "label": "Non-Negotiables", "fieldtype": "Text"},
        {"fieldname": "active", "label": "Active", "fieldtype": "Check", "default": "1"},
    ],
    "permissions": [
        _perm(ROLE_NAME, read=1),
        # No delete, even for System Manager — a decommissioned persona
        # is disabled (the `active` field), not removed, keeping history
        # intact. See bot-doctypes-design.md's permission matrix.
        _perm("System Manager", read=1, write=1, create=1),
    ],
}

SESSION = {
    "doctype": "DocType",
    "name": "Qkeee Bot Session",
    "module": "Custom",
    "custom": 1,
    "naming_rule": "Random",
    "autoname": "hash",
    "track_changes": 0,
    "fields": [
        {"fieldname": "user", "label": "User", "fieldtype": "Link", "options": "User",
         "reqd": 1, "in_list_view": 1},
        {"fieldname": "persona", "label": "Persona", "fieldtype": "Link",
         "options": "Qkeee Bot Persona", "reqd": 1, "in_list_view": 1},
        {"fieldname": "environment_tag", "label": "Environment Tag", "fieldtype": "Data",
         "reqd": 1},
        {"fieldname": "mode", "label": "Mode", "fieldtype": "Select",
         "options": "Read Only\nRead Write"},
        {"fieldname": "debug_mode", "label": "Debug Mode", "fieldtype": "Check"},
        {"fieldname": "started_on", "label": "Started On", "fieldtype": "Datetime"},
        {"fieldname": "ended_on", "label": "Ended On", "fieldtype": "Datetime"},
        {"fieldname": "status", "label": "Status", "fieldtype": "Select",
         "options": "Active\nClosed\nError", "default": "Active", "in_list_view": 1},
    ],
    "permissions": [
        _perm(ROLE_NAME, read=1, write=1, create=1),
        _perm("System Manager", read=1),
    ],
}

# linked_audit_log (Link -> Qkeee Bot Audit Log) is NOT in this create
# payload's fields, even though the design doc lists it as a Message
# field — live-confirmed against demo.qkeee.in that Frappe
# rejects a DocType create whose Link field's `options` names a doctype
# that doesn't exist yet (WrongOptionsDoctypeLinkError on
# "Qkeee Bot Message" for exactly this field). Since Message is created before Audit Log (ALL_DOCTYPES
# order), the field is added as a post-create `update` patch once Audit
# Log exists instead — see DEFERRED_FIELD_PATCHES below and
# init_bot.py's ensure_deferred_fields().
MESSAGE_LINKED_AUDIT_LOG_FIELD = {
    "fieldname": "linked_audit_log", "label": "Linked Audit Log", "fieldtype": "Link",
    "options": "Qkeee Bot Audit Log",
}

MESSAGE = {
    "doctype": "DocType",
    "name": "Qkeee Bot Message",
    "module": "Custom",
    "custom": 1,
    "naming_rule": "Random",
    "autoname": "hash",
    "track_changes": 0,
    "fields": [
        {"fieldname": "session", "label": "Session", "fieldtype": "Link",
         "options": "Qkeee Bot Session", "reqd": 1, "in_list_view": 1},
        {"fieldname": "speaker", "label": "Speaker", "fieldtype": "Select",
         "options": "User\nBot Analysis\nBot Response\nBot Action\nSystem",
         "reqd": 1, "in_list_view": 1},
        {"fieldname": "content", "label": "Content", "fieldtype": "Long Text", "reqd": 1},
        {"fieldname": "related_capability", "label": "Related Capability", "fieldtype": "Data"},
        {"fieldname": "in_reply_to", "label": "In Reply To", "fieldtype": "Link",
         "options": "Qkeee Bot Message"},
        # linked_audit_log deliberately omitted — see comment above.
    ],
    "permissions": [
        # create-only: no "write" flag, so a row can't be edited after insert.
        _perm(ROLE_NAME, read=1, create=1),
        _perm("System Manager", read=1),
    ],
}

# Fields added via a post-create `update` patch, once the doctype they
# link to actually exists. Each entry: which doctype to patch, the field
# definition to append, and which doctype must exist first.
DEFERRED_FIELD_PATCHES = [
    {
        "doctype": "Qkeee Bot Message",
        "field": MESSAGE_LINKED_AUDIT_LOG_FIELD,
        "requires": "Qkeee Bot Audit Log",
    },
]

AUDIT_LOG = {
    "doctype": "DocType",
    "name": "Qkeee Bot Audit Log",
    "module": "Custom",
    "custom": 1,
    "naming_rule": "Random",
    "autoname": "hash",
    "is_submittable": 1,
    "track_changes": 0,
    "fields": [
        {"fieldname": "session", "label": "Session (raw id)", "fieldtype": "Data",
         "reqd": 1, "in_list_view": 1,
         "description": "Raw session-id string, not a Link — Qkeee Bot Session may not "
                         "exist outside debug mode. See design doc decision 10."},
        {"fieldname": "persona_code", "label": "Persona Code", "fieldtype": "Data"},
        {"fieldname": "environment_tag", "label": "Environment Tag", "fieldtype": "Data"},
        {"fieldname": "triggering_message", "label": "Triggering Message", "fieldtype": "Link",
         "options": "Qkeee Bot Message"},
        {"fieldname": "action", "label": "Action", "fieldtype": "Select",
         "options": "Read\nCreate\nUpdate\nSubmit\nCancel\nDelete", "reqd": 1,
         "in_list_view": 1},
        {"fieldname": "reference_doctype", "label": "Reference DocType", "fieldtype": "Link",
         "options": "DocType", "reqd": 1, "in_list_view": 1},
        {"fieldname": "reference_name", "label": "Reference Name", "fieldtype": "Dynamic Link",
         "options": "reference_doctype", "in_list_view": 1},
        {"fieldname": "requested_by", "label": "Requested By", "fieldtype": "Link",
         "options": "User", "reqd": 1, "in_list_view": 1},
        {"fieldname": "timestamp", "label": "Timestamp", "fieldtype": "Datetime", "reqd": 1,
         "in_list_view": 1},
        {"fieldname": "status", "label": "Status", "fieldtype": "Select",
         "options": "Attempted\nSuccess\nFailure", "reqd": 1, "in_list_view": 1},
        {"fieldname": "error_detail", "label": "Error Detail", "fieldtype": "Small Text"},
        {"fieldname": "payload_before", "label": "Payload Before (JSON)", "fieldtype": "Long Text"},
        {"fieldname": "payload_after", "label": "Payload After (JSON)", "fieldtype": "Long Text"},
        {"fieldname": "field_diff", "label": "Field Diff (JSON)", "fieldtype": "Long Text"},
        {"fieldname": "audit_comment_posted", "label": "Audit Comment Posted", "fieldtype": "Check"},
        {"fieldname": "user_approved", "label": "User Approved", "fieldtype": "Select",
         "options": "Not Required\nApproved\nNot Confirmed", "reqd": 1,
         "default": "Not Required", "in_list_view": 1,
         "description": "Set by the caller, not inferred. 'Not Required' is reserved for "
                         "Read rows (auto). Every Create/Update/Submit/Cancel row must be "
                         "explicitly 'Approved' (caller passed user_approved=True after a "
                         "real confirm) or 'Not Confirmed' (caller didn't) — this is a "
                         "detection field for scanning whether the six-stage confirm-before-"
                         "Execute discipline was actually followed, not a write-blocking gate; "
                         "the write still proceeds either way so the violation is visible "
                         "here instead of silently prevented."},
        {"fieldname": "approval_note", "label": "Approval Note", "fieldtype": "Small Text",
         "description": "Free text of what was confirmed, e.g. 'user confirmed JE draft "
                         "JE-0001, balanced, before submit' — populated by the calling "
                         "skill's confirm-stage renderer where one exists."},
    ],
    "permissions": [
        _perm(ROLE_NAME, read=1, write=1, create=1, submit=1),
        _perm("System Manager", read=1),
    ],
}

# Create order matters: Persona has no doctype deps, Session links to
# Persona, Message links to Session (+ self), Audit Log links to Message
# (already exists by then) and to DocType (always exists). Audit Log's
# link to Message and Message's link to Audit Log are mutually
# referential — live-confirmed against demo.qkeee.in that
# Frappe does NOT allow a Link field naming a not-yet-existing doctype at
# DocType-create time (WrongOptionsDoctypeLinkError), so the two can't
# both be created with their cross-reference fields present. Audit Log's
# `triggering_message` field is fine as-is (Message already exists by the
# time Audit Log is created); Message's `linked_audit_log` field is
# deferred and patched in via DEFERRED_FIELD_PATCHES above, after Audit
# Log exists — see init_bot.py's ensure_deferred_fields().
ALL_DOCTYPES = [PERSONA, SESSION, MESSAGE, AUDIT_LOG]
