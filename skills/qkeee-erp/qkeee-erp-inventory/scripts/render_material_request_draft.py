#!/usr/bin/env python3
"""
qkeee-erp-inventory — Material Request (reorder trigger) draft renderer.

Lower-risk than a Stock Entry or Stock Reconciliation — a Material
Request doesn't move or revalue stock by itself, it's a request for
someone else (Procurement, or Manufacturing) to act on. Single-confirm,
not the double-confirm reserved for this skill's book-value/write-off
capabilities. Still staged, never created inline, per the library-wide
six-stage capability pattern.

This script NEVER calls ERPNext. It only formats a draft for human
review. Actually creating the Material Request is a separate, later
step gated on explicit confirmation of this rendered draft.
"""

import json
import sys

REQUEST_TYPES = ("Purchase", "Material Transfer", "Material Issue", "Manufacture", "Customer Provided")
REQUIRED_ITEM_KEYS = ("item_code", "qty", "schedule_date")


class RenderError(Exception):
    pass


def _fmt(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value) if value not in (None, "") else "-"


def render_material_request_draft(material_request_type: str, company: str,
                                   transaction_date: str, items: list, notes: str = "") -> str:
    """
    items: list of {"item_code": str, "qty": num, "schedule_date": str,
      "warehouse": str (optional but recommended — target warehouse for
      a Purchase-type request)}.

    Refuses to render "ready" if material_request_type is unrecognized,
    items is empty, or any item is missing item_code/qty/schedule_date.
    """
    if material_request_type not in REQUEST_TYPES:
        raise RenderError(f"material_request_type must be one of {REQUEST_TYPES}, got {material_request_type!r}")
    if not items:
        raise RenderError("items is empty — a Material Request needs at least one line.")

    issues = []
    for it in items:
        missing = [k for k in REQUIRED_ITEM_KEYS if not it.get(k)]
        if missing:
            issues.append(f"{it.get('item_code', '?')}: missing required field(s) {missing}.")
        elif it["qty"] <= 0:
            issues.append(f"{it.get('item_code', '?')}: qty {_fmt(it['qty'])} must be greater than zero.")
        if material_request_type == "Purchase" and not it.get("warehouse"):
            issues.append(
                f"{it.get('item_code', '?')}: no target warehouse stated — recommended for a "
                f"Purchase request so received stock lands somewhere specific."
            )

    is_ready = not issues

    lines = [
        f"# Material Request ({material_request_type}) — DRAFT, NOT CREATED",
        "",
        f"**Company:** {company}  |  **Transaction date:** {transaction_date}",
        "",
        "| Item | Qty | Needed by | Warehouse |",
        "| --- | ---: | --- | --- |",
    ]
    for it in items:
        lines.append(
            f"| {it.get('item_code', '?')} | {_fmt(it.get('qty'))} | "
            f"{_fmt(it.get('schedule_date'))} | {_fmt(it.get('warehouse'))} |"
        )
    lines.append("")

    if is_ready:
        lines.append(
            "**Status: READY. Staged for review — not yet created in ERPNext. Explicit "
            "confirmation required before Execute.**"
        )
    else:
        lines.append(f"**Status: INCOMPLETE — {len(issues)} issue(s) below.**")
        lines.append("")
        lines.append("## Issues")
        for i in issues:
            lines.append(f"- {i}")
    lines.append("")

    if notes:
        lines += ["## Notes", "", notes, ""]

    lines += [
        "---",
        "*A drafted Material Request is not itself a commitment — it hands off to "
        "Procurement (Purchase type) or Manufacturing, user-routed, no direct mechanism "
        "in this library.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_material_request_draft.py <path-to-json-input>", file=sys.stderr)
        print(
            'JSON shape: {"material_request_type": "...", "company": "...", '
            '"transaction_date": "...", "items": [{"item_code": "...", "qty": 0, '
            '"schedule_date": "...", "warehouse": "..."}], "notes": "..."}',
            file=sys.stderr,
        )
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
        out = render_material_request_draft(
            material_request_type=payload["material_request_type"],
            company=payload["company"],
            transaction_date=payload["transaction_date"],
            items=payload["items"],
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
