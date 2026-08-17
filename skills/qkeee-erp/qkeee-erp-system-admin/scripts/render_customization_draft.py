#!/usr/bin/env python3
"""
qkeee-erp-system-admin — DocType customization draft renderer (simple
cases: adding a Custom Field, or changing one Property Setter value).

Per the module plan: simple customization cases are applied directly
(through this renderer's confirm gate); complex cases (anything
touching layout/scripting beyond one field or one property) get
step-by-step guidance instead — this renderer refuses to render for
anything beyond its two supported kinds, forcing the calling skill to
fall back to guidance rather than improvising a payload.

Live finding (confirmed against <erp-instance>): after a Custom Field
create, `GET /api/resource/DocType/<dt>` does NOT reflect the new field
— DocType meta is cached server-side, and `frappe.clear_cache` is not a
whitelisted method over this REST API (403 even as Administrator).
Verify a Custom Field's creation by querying the Custom Field resource
directly by its name (`<dt>-<fieldname>`), never by re-fetching DocType
meta — this renderer's output says so explicitly so a caller doesn't
mistake the cache lag for a failed create.

This script NEVER calls ERPNext. It only formats the confirmation.
"""

import json
import re
import sys

KINDS = ("custom_field", "property_setter")
FIELDNAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class RenderError(Exception):
    pass


def render_customization_draft(kind: str, dt: str, reason: str,
                                 fieldname: str = None, label: str = None, fieldtype: str = None,
                                 insert_after: str = None, existing_fieldnames: list = None,
                                 property: str = None, new_value=None, current_value=None,
                                 notes: str = "") -> str:
    if kind not in KINDS:
        raise RenderError(
            f"kind must be one of {KINDS}, got {kind!r} — anything beyond a single Custom "
            "Field add or a single Property Setter value change is a complex case: give "
            "the user step-by-step guidance instead of using this renderer."
        )
    if not dt:
        raise RenderError("dt (target DocType name) is required.")
    if not reason:
        raise RenderError("reason is required — state why this customization is needed.")

    blocked = []
    lines = [f"# DocType customization draft — `{kind}` on `{dt}`", ""]

    if kind == "custom_field":
        if not fieldname:
            blocked.append("fieldname is required.")
        elif not FIELDNAME_RE.match(fieldname):
            blocked.append(f"fieldname {fieldname!r} must be lowercase snake_case "
                            "(start with a letter, then letters/digits/underscores) — "
                            "matches ERPNext's own convention and avoids a field that's "
                            "awkward to reference later.")
        if not label:
            blocked.append("label is required.")
        if not fieldtype:
            blocked.append("fieldtype is required (e.g. Data, Link, Select, Check, Date).")
        if existing_fieldnames is not None and fieldname in existing_fieldnames:
            blocked.append(
                f"fieldname {fieldname!r} already exists on `{dt}` (per the caller-supplied "
                "existing_fieldnames list) — creating it again would either fail or silently "
                "shadow the existing field depending on ERPNext version. Pick a different "
                "name or confirm this is meant to update the existing field's properties "
                "instead (a property_setter change, not a new custom_field)."
            )

        lines += [
            f"**Field:** `{fieldname}`  |  **Label:** {label}  |  **Type:** {fieldtype}",
            f"**Insert after:** {insert_after or '(end of form)'}",
            f"**Reason:** {reason}",
            "",
        ]

    else:  # property_setter
        if not property:
            blocked.append("property is required (the DocField/DocType property being overridden).")
        if new_value is None:
            blocked.append("new_value is required.")

        lines += [
            f"**Property:** `{property}`",
            f"**Current value:** {current_value if current_value is not None else '(unknown — fetch the existing Property Setter/DocType meta first)'}",
            f"**New value:** {new_value}",
            f"**Reason:** {reason}",
            "",
        ]

    ready = not blocked
    if blocked:
        lines.append(f"**BLOCKED ({len(blocked)}):**")
        for b in blocked:
            lines.append(f"- {b}")
        lines.append("")

    lines.append(f"**Ready to apply:** {'YES' if ready else 'NO — see BLOCKED above'}")
    lines += [
        "",
        "**Verification after create/update:** re-query the "
        f"`{'Custom Field' if kind == 'custom_field' else 'Property Setter'}` resource "
        "directly by its own name, NOT `DocType/<dt>` meta — the meta cache does not "
        "reflect a fresh customization over this REST API and `frappe.clear_cache` is not "
        "callable here (confirmed live). A caller re-checking via DocType meta will see "
        "the field 'missing' even though it was created successfully.",
    ]

    if notes:
        lines += ["", "## Notes", "", notes]

    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_customization_draft.py <path-to-json-input>", file=sys.stderr)
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
        out = render_customization_draft(
            kind=payload["kind"],
            dt=payload["dt"],
            reason=payload.get("reason", ""),
            fieldname=payload.get("fieldname"),
            label=payload.get("label"),
            fieldtype=payload.get("fieldtype"),
            insert_after=payload.get("insert_after"),
            existing_fieldnames=payload.get("existing_fieldnames"),
            property=payload.get("property"),
            new_value=payload.get("new_value"),
            current_value=payload.get("current_value"),
            notes=payload.get("notes", ""),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(out)


if __name__ == "__main__":
    _cli()
