#!/usr/bin/env python3
"""
qkeee-erp-frappe-core — advisory-first draft renderer.

Formats the exact payload a create/update/submit/cancel/delete is about
to send, marks which fields came from confirmed live metadata
(discover.py meta/resolve) vs. which are inferred from the user's
request, and computes the confirmation token erp_client.gated_mutate_resource()
requires. This script NEVER calls ERPNext — it only formats a draft
already assembled by the caller and shows it to the user before anything
is written, per this skill's "advisory-first, always" non-negotiable
(SKILL.md) — unconditional here, unlike the narrower per-capability
gating in system-admin/fixed-asset-manager.
"""

import json
import sys
import time

from confirm_token import DEFAULT_TOKEN_TTL_SECONDS, advisory_write_token

ACTIONS = ("create", "update", "submit", "cancel", "delete")


class RenderError(Exception):
    pass


def render_draft(action: str, doctype: str, payload: dict, requested_by: str,
                  name: str = None, confirmed_fields: list = None,
                  inferred_fields: list = None, notes: str = "",
                  issued_at: int = None) -> str:
    """
    action: one of ACTIONS above.
    doctype: the target DocType — should already have been through
      `discover.py resolve <doctype>` before this is called.
    payload: the exact field values about to be sent (create/update) —
      may be empty for submit/cancel/delete, where `name` is what matters.
    name: the record name, required for update/submit/cancel/delete.
    confirmed_fields: field names in `payload` that came straight out of
      `discover.py meta`'s live field list — i.e. known to exist on this
      instance's DocType with this fieldtype/options.
    inferred_fields: field names in `payload` that the skill is proposing
      based on the user's request but did NOT independently confirm
      against live meta (e.g. a value the user stated verbatim that maps
      to a field discovered in a prior turn but not re-checked this
      call). Every key in `payload` should land in exactly one of
      confirmed_fields/inferred_fields — an unclassified field is treated
      as inferred (the more cautious default) and flagged as such.
    requested_by: ERPNext user id/email this write is attributed to.
    """
    if action not in ACTIONS:
        raise RenderError(f"action must be one of {ACTIONS}, got {action!r}")
    if not doctype:
        raise RenderError("doctype is required.")
    if action != "create" and not name:
        raise RenderError(f"name is required for action={action!r}.")
    if not requested_by:
        raise RenderError("requested_by is required — who is this write attributed to?")

    payload = payload or {}
    confirmed_fields = set(confirmed_fields or [])
    inferred_fields = set(inferred_fields or [])
    unclassified = set(payload.keys()) - confirmed_fields - inferred_fields
    inferred_fields = inferred_fields | unclassified

    issued_at = int(issued_at) if issued_at is not None else int(time.time())
    token = advisory_write_token(action, doctype, name, payload, requested_by, issued_at)

    lines = [
        f"# Advisory draft — {action.upper()} `{doctype}`" + (f" `{name}`" if name else ""),
        "",
        "**Status: NOT DONE YET. This is a staged draft, not an action already taken.** "
        "qkeee-erp-frappe-core stages every write regardless of qkeee_erp.mode, since this "
        "doctype hasn't had the design-time review the named persona skills' capabilities "
        "have — confirm this exact draft with the user before calling "
        "`gated_mutate_resource()`.",
        "",
        f"**Action:** {action}  |  **DocType:** `{doctype}`" + (f"  |  **Record:** `{name}`" if name else ""),
        f"**Requested by:** {requested_by}",
        "",
    ]

    if payload:
        lines.append("**Payload:**")
        lines.append("```json")
        lines.append(json.dumps(payload, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
        if confirmed_fields:
            lines.append(
                "**Confirmed against live metadata** (discover.py meta/resolve): "
                + ", ".join(f"`{f}`" for f in sorted(confirmed_fields & set(payload.keys())))
            )
        if inferred_fields:
            lines.append(
                "**Inferred / not independently re-confirmed this call** — treat with more "
                "caution, double-check before confirming: "
                + ", ".join(f"`{f}`" for f in sorted(inferred_fields & set(payload.keys())))
            )
        lines.append("")
    else:
        lines.append(f"**No field payload** — {action} acts on the existing record `{name}` "
                      "as-is (submit/cancel/delete carry no new field values).")
        lines.append("")

    if action == "submit":
        lines.append(
            "**Reminder:** submit is step 3 of save-draft-then-review-then-submit — this "
            "draft should already have been through `erp_client.py get` to verify every "
            "Link field resolves to a real record before this render was requested."
        )
        lines.append("")
    elif action == "delete":
        lines.append(
            "**Reminder:** delete after cancel is not reliably usable for ledger-touching "
            "doctypes (LinkExistsError) — confirm this is actually deletable, not just "
            "cancellable, before proceeding."
        )
        lines.append("")

    if notes:
        lines += ["## Notes", "", notes, ""]

    lines += [
        f"**Confirmation token:** `{token}`  |  **Issued at:** {issued_at} (epoch seconds) — "
        "pass BOTH to `erp_client.gated_mutate_resource()`. The call is refused without a "
        "matching token, and refused if issued_at is more than "
        f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes old — re-render this draft if too much "
        "time has passed since it was shown.",
    ]

    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_draft.py <path-to-json-input>", file=sys.stderr)
        sys.exit(2)

    try:
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            spec = json.load(fh)
    except FileNotFoundError:
        print(f"ERROR: input file not found: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: {sys.argv[1]} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        out = render_draft(
            action=spec["action"],
            doctype=spec["doctype"],
            payload=spec.get("payload", {}),
            requested_by=spec["requested_by"],
            name=spec.get("name"),
            confirmed_fields=spec.get("confirmed_fields"),
            inferred_fields=spec.get("inferred_fields"),
            notes=spec.get("notes", ""),
            issued_at=spec.get("issued_at"),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(out)


if __name__ == "__main__":
    _cli()
