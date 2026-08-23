"""
Create-payloads for the 2 Qkeee Bot audit-trail doctypes + the Qkeee Bot
role. Synced from qkeee-erp-bot-init/references/bot-doctypes-design.md —
that file is the design source of truth; if you're changing field shapes,
update the design doc first, then this file to match.

Both doctypes are created as custom (custom=1) records attached to
Frappe's built-in "Custom" module — no app, no module folder, no Python
controller. See the design doc's "Why no app" section.
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
         "description": "Raw session-id string, not a Link — a plain correlator the "
                         "caller passes as-is. See design doc decision 10."},
        {"fieldname": "persona_code", "label": "Persona Code", "fieldtype": "Data"},
        {"fieldname": "environment_tag", "label": "Environment Tag", "fieldtype": "Data"},
        {"fieldname": "channel", "label": "Channel", "fieldtype": "Select",
         "options": "\nWeb\nDiscord\nTelegram\nWhatsApp\nEmail\nSlack\nCLI\nAPI\nOther",
         "in_list_view": 1,
         "description": "Denormalized from Session where one exists; settable directly "
                         "otherwise — see design doc."},
        {"fieldname": "channel_metadata", "label": "Channel Metadata (JSON)", "fieldtype": "Long Text",
         "description": "Free-form per-channel tracing detail, e.g. chat_id, message_id, "
                         "thread id, email Message-Id header — see bot-doctypes-design.md."},
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

# Create order: Persona has no doctype deps and is created first; Audit
# Log's only doctype-link fields (reference_doctype -> DocType,
# requested_by -> User) always already exist, so it has no ordering
# dependency on Persona either — this list order is just Persona-before-
# Audit-Log for readability, not a Frappe requirement.
ALL_DOCTYPES = [PERSONA, AUDIT_LOG]

# The persona_code/persona_label/default_mode this library ships today —
# hand-maintained, same as PERSONA_SKILLS in
# qkeee-erp-frappe-core/scripts/sync_to_personas.py (that list drives the
# connector-file sync; this one drives init_bot.py's ensure_personas()
# step). Kept in sync by hand when a persona skill is added/removed —
# no automated derivation exists yet for either list. Every persona ships
# read-only as its default_mode today; a future persona whose default
# should be read-write needs its own entry here, not a blanket assumption.
PERSONA_MANIFEST = [
    {"persona_code": "qkeee-erp-accounts-executive", "persona_label": "Accounts Executive"},
    {"persona_code": "qkeee-erp-fixed-asset-manager", "persona_label": "Fixed Asset Manager"},
    {"persona_code": "qkeee-erp-hr-associate", "persona_label": "HR Associate"},
    {"persona_code": "qkeee-erp-inventory", "persona_label": "Inventory"},
    {"persona_code": "qkeee-erp-mis-analyst", "persona_label": "MIS Analyst"},
    {"persona_code": "qkeee-erp-procurement", "persona_label": "Procurement"},
    {"persona_code": "qkeee-erp-sales", "persona_label": "Sales"},
    {"persona_code": "qkeee-erp-system-admin", "persona_label": "System Admin"},
]
