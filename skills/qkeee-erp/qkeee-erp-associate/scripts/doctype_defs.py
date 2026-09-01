"""
Create-payloads for the Qkeee Bot audit-trail doctype + the Qkeee Bot
role. A qkeee-erp-associate-specific design doc for these field shapes
doesn't exist yet — this module is the source of truth for now; if you're
changing field shapes, update it directly.

CODE-ONLY: this module has never been run against a live instance in this
form.

The doctype is created as a custom (custom=1) record attached to Frappe's
built-in "Custom" module — no app, no module folder, no Python
controller.
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
                         "caller passes as-is."},
        {"fieldname": "domain_code", "label": "Domain Code", "fieldtype": "Data",
         "description": "Denormalized string (no doctype join) naming the active "
                         "qkeee-erp-associate domain reference that made this call (e.g. "
                         "'qkeee-erp-associate/hr-payroll'). A live schema migration "
                         "(renaming an existing field on an already-provisioned "
                         "instance) is NOT performed by this code — reconciling an "
                         "already-provisioned instance's field name is a manual, "
                         "deliberate action for whoever operates that instance."},
        {"fieldname": "environment_tag", "label": "Environment Tag", "fieldtype": "Data"},
        {"fieldname": "channel", "label": "Channel", "fieldtype": "Select",
         "options": "\nWeb\nDiscord\nTelegram\nWhatsApp\nEmail\nSlack\nCLI\nAPI\nOther",
         "in_list_view": 1,
         "description": "Denormalized from Session where one exists; settable directly "
                         "otherwise."},
        {"fieldname": "channel_metadata", "label": "Channel Metadata (JSON)", "fieldtype": "Long Text",
         "description": "Free-form per-channel tracing detail, e.g. chat_id, message_id, "
                         "thread id, email Message-Id header."},
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
                         "detection field for scanning whether the confirm-before-Execute "
                         "discipline was actually followed, not a write-blocking gate; "
                         "the write still proceeds either way so the violation is visible "
                         "here instead of silently prevented."},
        {"fieldname": "approval_note", "label": "Approval Note", "fieldtype": "Small Text",
         "description": "Free text of what was confirmed, e.g. 'user confirmed JE draft "
                         "JE-0001, balanced, before submit' — populated by the calling "
                         "domain's confirm-stage renderer where one exists."},
    ],
    "permissions": [
        _perm(ROLE_NAME, read=1, write=1, create=1, submit=1),
        _perm("System Manager", read=1),
    ],
}

# Audit Log is the only doctype here, so there is no create-order question
# to document — kept as a single-element list for init_bot.py's existing
# iteration shape.
ALL_DOCTYPES = [AUDIT_LOG]
