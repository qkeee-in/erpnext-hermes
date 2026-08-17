#!/usr/bin/env python3
"""
qkeee-erp-system-admin — role/permission change confirmation renderer.

The non-negotiable: any permission/role change must be scoped
explicitly and confirmed — never broad or implicit ("give them admin
access" must resolve to specific roles/rights, not a blanket grant).
Permission changes get a DOUBLE confirm (state the exact before/after,
then ask again), same class of action as
qkeee-erp-fixed-asset-manager's depreciation-run/disposal gate — a
permission grant is not itself irreversible, but a silently-broad one
is a real security regression, which this skill's design note calls
out as the thing to scrutinize hardest.

Four actions, confirmed live against <erp-instance> via
frappe.core.page.permission_manager.permission_manager:
  - "add"    — creates a bare new permission row (role, permlevel) with
               every right OFF. Never grants anything by itself; the
               rights come from a follow-up "update" per right.
  - "update" — flips ONE right (ptype, e.g. "write") to a new value on
               an existing row. This is the one that actually grants or
               revokes something — states current -> new explicitly.
  - "remove" — deletes the entire permission row for (doctype, role,
               permlevel), removing every right that row carried.
  - "reset"  — wipes ALL custom overrides for the doctype back to
               ERPNext's shipped defaults. The single most blast-radius
               call in this skill (loses every custom grant/restriction
               ever made for that doctype, not just one role's) — always
               rendered with the loudest possible warning.

This script NEVER calls ERPNext. It only formats the confirmation.
"""

import json
import sys
import time

from confirm_token import DEFAULT_TOKEN_TTL_SECONDS, permission_change_token

ACTIONS = ("add", "update", "remove", "reset")


class RenderError(Exception):
    pass


def render_permission_change(action: str, doctype: str, role: str = "", permlevel: int = 0,
                              ptype: str = None, current_value=None, new_value=None,
                              current_row: dict = None, reason: str = "", notes: str = "",
                              issued_at: int = None) -> str:
    """
    action == "update": ptype and new_value are required; current_value
      should be the row's actual current value for that ptype (fetched
      via erp_client.get_permissions(), never guessed) so the diff shown
      is real, not assumed.
    action == "remove": current_row (the full permission row being
      deleted, from get_permissions()) should be supplied so every right
      about to be lost is listed explicitly, not just implied.
    action == "reset": doctype only — role/permlevel/ptype are ignored.
    reason is required for every action — "why", not just "what".
    """
    if action not in ACTIONS:
        raise RenderError(f"action must be one of {ACTIONS}, got {action!r}")
    if not doctype:
        raise RenderError("doctype is required.")
    if not reason:
        raise RenderError(
            "reason is required — a permission change confirmation must state why, "
            "not just what changes."
        )
    if action != "reset" and not role:
        raise RenderError(f"role is required for action={action!r}.")
    if action == "update":
        if not ptype:
            raise RenderError("ptype is required for action='update' (e.g. 'write', 'create', 'submit').")
        if new_value not in (0, 1):
            raise RenderError(f"new_value must be 0 or 1 for action='update', got {new_value!r}")

    issued_at = int(issued_at) if issued_at is not None else int(time.time())
    token = permission_change_token(action, doctype, role, permlevel, ptype or "", new_value, issued_at)

    lines = [
        f"# Permission change confirmation — `{doctype}`" + (f" / role `{role}`" if role else ""),
        "",
        "**Status: NOT APPLIED YET. This is a confirmation request, not a change already "
        "made. Double confirm required per this skill's non-negotiable: show this, then ask "
        "again before calling the permission-manager method.**",
        "",
        f"**Action:** {action}  |  **Permission level:** {permlevel}",
        f"**Reason:** {reason}",
        "",
    ]

    if action == "add":
        lines += [
            f"**What happens:** a new, empty permission row is created for role `{role}` "
            f"on `{doctype}` at permission level {permlevel} — every right (read/write/"
            "create/delete/submit/...) starts OFF. This grants nothing by itself; the "
            "actual grant happens in a follow-up 'update' call per right, which needs its "
            "own separate confirmation.",
        ]
    elif action == "update":
        verdict = "GRANTING" if new_value == 1 else "REVOKING"
        lines += [
            f"**Right:** `{ptype}`",
            f"**Current value:** {current_value if current_value is not None else '(unknown — fetch get_permissions() first)'}",
            f"**New value:** {new_value}  —  **{verdict} `{ptype}` for role `{role}` on `{doctype}` "
            f"(permlevel {permlevel})**",
        ]
    elif action == "remove":
        lines += [f"**What happens:** the CUSTOM OVERRIDE permission row for role `{role}` on "
                   f"`{doctype}` (permlevel {permlevel}) is deleted — every right that "
                   "override row currently carries is removed at once, not just one.",
                   "**IMPORTANT — this may not fully revoke access:** `remove` only deletes "
                   "the Custom DocPerm override; it does not touch ERPNext's shipped/standard "
                   "DocPerm row for this doctype+role, if one exists. If the standard role "
                   "definition already grants this right independent of the override, the "
                   "role will STILL have it after this remove — verify with a fresh "
                   "`get_permissions()` call after applying, don't assume 'removed' means "
                   "'revoked'."]
        if current_row:
            granted = [k for k, v in current_row.items()
                       if k in ("read", "write", "create", "delete", "submit", "cancel",
                                 "amend", "report", "export", "import", "share", "print", "email")
                       and v]
            lines.append(f"**Rights currently granted by this row (all lost on remove):** "
                          f"{', '.join(granted) if granted else '(none — row grants nothing already)'}")
        else:
            lines.append("**WARNING:** current_row was not supplied — this confirmation cannot "
                          "state which rights are about to be lost. Fetch get_permissions() first "
                          "and pass the matching row before proceeding.")
    else:  # reset
        lines += [
            "**DANGER — WIDEST BLAST RADIUS ACTION THIS SKILL CAN TAKE.** This wipes EVERY "
            f"custom permission override ever made for `{doctype}` — for ALL roles, not just "
            "one — back to ERPNext's shipped defaults. There is no per-role or per-right undo "
            "after this; every custom grant AND every custom restriction on this doctype is "
            "gone at once. Confirm the user actually means the whole doctype, not one role's "
            "access, before proceeding.",
        ]

    lines += [
        "",
        f"**Confirmation token:** `{token}`  |  **Issued at:** {issued_at} (epoch seconds) — "
        "pass BOTH to `erp_client.call_permission_manager()`. The call is refused without a "
        "matching token, and refused if issued_at is more than "
        f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes old — re-render this confirmation if too "
        "much time has passed since it was shown.",
    ]

    if notes:
        lines += ["", "## Notes", "", notes]

    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_permission_change.py <path-to-json-input>", file=sys.stderr)
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
        out = render_permission_change(
            action=payload["action"],
            doctype=payload["doctype"],
            role=payload.get("role", ""),
            permlevel=payload.get("permlevel", 0),
            ptype=payload.get("ptype"),
            current_value=payload.get("current_value"),
            new_value=payload.get("new_value"),
            current_row=payload.get("current_row"),
            reason=payload.get("reason", ""),
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
