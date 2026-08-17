#!/usr/bin/env python3
"""
qkeee-erp-system-admin — destructive-action confirmation renderer.

The non-negotiable: destructive actions (disable a user, delete a
DocType customization) must be scoped explicitly and confirmed. Gets a
DOUBLE confirm, same class as permission changes — state exactly what's
being disabled/deleted and why, then ask again.

Covers: disabling or deleting a User, and deleting a Custom Field,
Property Setter, Webhook, or Workflow. NOT for deleting core ERPNext
transactional data (that's each transactional persona skill's own
domain, e.g. qkeee-erp-fixed-asset-manager's disposal) — this is
strictly the system-configuration surface.

This script NEVER calls ERPNext. It only formats the confirmation.
"""

import json
import sys
import time

from confirm_token import DEFAULT_TOKEN_TTL_SECONDS, destructive_action_token

ACTIONS = ("disable_user", "delete_user", "delete_custom_field",
           "delete_property_setter", "delete_webhook", "delete_workflow")

TARGET_DOCTYPE = {
    "disable_user": "User",
    "delete_user": "User",
    "delete_custom_field": "Custom Field",
    "delete_property_setter": "Property Setter",
    "delete_webhook": "Webhook",
    "delete_workflow": "Workflow",
}


class RenderError(Exception):
    pass


def render_destructive_action(action: str, target_name: str, reason: str,
                               impact_notes: str = "", notes: str = "",
                               issued_at: int = None) -> str:
    """
    action: one of ACTIONS above.
    target_name: the exact record name being disabled/deleted (a
      user's email, a Custom Field's "<DocType>-<fieldname>" name,
      etc) — never a description, the literal ERPNext record name.
    reason: required — why, not just what. A bare "clean up" or
      "not needed" is accepted (this renderer doesn't judge quality of
      reason, only that one was stated), but an empty reason is refused.
    impact_notes: caller-supplied specifics about what this action
      actually affects (e.g. "user has 12 open ToDos assigned",
      "field is referenced in 3 Print Formats") — surfaced prominently
      if given; the renderer does not fetch this itself (no network).
    """
    if action not in ACTIONS:
        raise RenderError(f"action must be one of {ACTIONS}, got {action!r}")
    if not target_name:
        raise RenderError("target_name is required.")
    if not reason:
        raise RenderError(
            "reason is required — a destructive-action confirmation must state why, "
            "not just what."
        )

    doctype = TARGET_DOCTYPE[action]
    issued_at = int(issued_at) if issued_at is not None else int(time.time())
    token = destructive_action_token(action, doctype, target_name, reason, issued_at)
    verb = {
        "disable_user": "DISABLE (login access removed, record kept)",
        "delete_user": "PERMANENTLY DELETE",
        "delete_custom_field": "PERMANENTLY DELETE",
        "delete_property_setter": "PERMANENTLY DELETE",
        "delete_webhook": "PERMANENTLY DELETE",
        "delete_workflow": "PERMANENTLY DELETE",
    }[action]

    lines = [
        f"# Destructive action confirmation — {verb} `{doctype}` `{target_name}`",
        "",
        "**Status: NOT DONE YET. This is a confirmation request, not an action already "
        "taken. Double confirm required per this skill's non-negotiable: show this, then "
        "ask again before calling the write.**",
        "",
        f"**Action:** {action}  |  **Target:** `{doctype}` `{target_name}`",
        f"**Reason:** {reason}",
        "",
    ]

    if action == "disable_user":
        lines.append(
            "**What happens:** the user's `enabled` field is set to 0 — they immediately "
            "lose login access. The User record itself, its role assignments, and every "
            "document they own/created are kept intact. This is reversible (re-enable), "
            "unlike delete — prefer disable over delete_user unless the account must be "
            "gone entirely."
        )
    elif action == "delete_user":
        lines.append(
            "**What happens:** the User record is permanently removed. Confirmed live: a "
            "never-referenced User deletes cleanly; a User who owns/created other records "
            "will likely fail with a LinkExistsError instead of silently orphaning that "
            "data — if that happens, disable_user is the practical alternative, not a "
            "forced delete."
        )
    else:
        lines.append(
            f"**What happens:** the `{doctype}` record `{target_name}` is permanently "
            "removed. If this customization is referenced elsewhere (a Print Format using "
            "the field, a workflow tied to this doctype), removal may change behavior "
            "beyond just this one record — that's what impact_notes below should cover."
        )

    if impact_notes:
        lines += ["", "**Known impact (caller-supplied, not independently verified by this "
                       "renderer):**", impact_notes]
    else:
        lines += ["", "**No impact_notes supplied** — this renderer has no network access and "
                       "cannot check what references this record. Check for dependents "
                       "(Print Formats, other customizations, linked documents) before "
                       "confirming if that's plausible for this target."]

    lines += [
        "",
        f"**Confirmation token:** `{token}`  |  **Issued at:** {issued_at} (epoch seconds) — "
        "pass BOTH to `erp_client.destructive_mutate()`. The call is refused without a "
        "matching token, and refused if issued_at is more than "
        f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes old — re-render this confirmation if too "
        "much time has passed since it was shown. On success, `reason` is also written onto "
        "the record as an ERPNext Comment (best-effort) so the audit trail isn't only in this "
        "chat transcript.",
    ]

    if notes:
        lines += ["", "## Notes", "", notes]

    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_destructive_action.py <path-to-json-input>", file=sys.stderr)
        sys.exit(2)

    try:
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        print(f"ERROR: input file not found: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: {sys.argv[1]} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        out = render_destructive_action(
            action=payload["action"],
            target_name=payload["target_name"],
            reason=payload.get("reason", ""),
            impact_notes=payload.get("impact_notes", ""),
            notes=payload.get("notes", ""),
            issued_at=payload.get("issued_at"),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(out)


if __name__ == "__main__":
    _cli()
