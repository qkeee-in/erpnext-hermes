#!/usr/bin/env python3
"""
qkeee-erp-procurement — Purchase Order draft renderer.

Confirmed live against <erp-instance>: ERPNext's own schema
does not flag a line item's `warehouse` as `reqd`, but `validate()`
throws ValidationError for any stock Item lacking a warehouse — a PO
built purely against the declared schema silently fails at create time.
This renderer checks the practical requirement (warehouse present for
every stock-tracked line), not just the declared-mandatory one.

Enforces this skill's submission-authority default in code: a rendered
draft's `recommended_action` is "create-as-draft-only" unless the caller
explicitly passes `submission_authority_confirmed=True` — see
references/domain-knowledge.md and SKILL.md's non-negotiable. This
script doesn't call ERPNext itself; it only decides what to recommend
and refuses to mark a draft "create-and-submit-ready" without that flag.
"""

import json
import sys

REQUIRED_HEADER_FIELDS = ("supplier", "company", "transaction_date", "currency")
REQUIRED_ITEM_FIELDS = ("item_code", "qty", "rate")


class RenderError(Exception):
    pass


def _fmt(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def render_po_draft(header: dict, items: list, stock_items: set = None,
                     submission_authority_confirmed: bool = False) -> dict:
    """
    header: PO header fields (supplier, company, transaction_date,
      currency, schedule_date, ...).
    items: list of {"item_code", "qty", "rate", "warehouse"?, ...}.
    stock_items: set of item_codes known to be stock-tracked (the caller
      should resolve this via a query against Item.is_stock_item first —
      a non-stock/service item doesn't need a warehouse). If not
      provided, every item is conservatively treated as stock-tracked
      (safer default: flag a missing warehouse rather than silently
      assume it's a service line).
    submission_authority_confirmed: True only if the calling skill has
      confirmed (via role check or explicit user statement) that the
      acting user holds PO-submission authority. Defaults False — the
      hard default from the module plan's Phase 6 decision is
      draft-only whenever this is unclear, not just "when unsure."

    Returns {"status": "ready"|"incomplete", "recommended_action": str,
             "markdown": str, "problems": [...]}.
    """
    stock_items = stock_items if stock_items is not None else {i.get("item_code") for i in items}
    problems = []

    for f in REQUIRED_HEADER_FIELDS:
        if not header.get(f):
            problems.append(f"header.{f}: missing")

    if not items:
        problems.append("items: at least one line item is required")

    total = 0.0
    for idx, item in enumerate(items, start=1):
        for f in REQUIRED_ITEM_FIELDS:
            if item.get(f) in (None, "", []):
                problems.append(f"item[{idx}] ({item.get('item_code', '?')}): {f} missing")
        if item.get("item_code") in stock_items and not item.get("warehouse"):
            problems.append(
                f"item[{idx}] ({item.get('item_code', '?')}): warehouse missing — ERPNext's "
                f"validate() rejects a stock item line with no warehouse at create time, "
                f"even though the schema doesn't flag it 'reqd' (confirmed live)"
            )
        qty = item.get("qty") or 0
        rate = item.get("rate") or 0
        if isinstance(qty, (int, float)) and isinstance(rate, (int, float)):
            total += qty * rate

    status = "incomplete" if problems else "ready"

    if status == "incomplete":
        recommended_action = "fix-before-staging"
    elif submission_authority_confirmed:
        recommended_action = "create-then-submit-on-confirm"
    else:
        recommended_action = "create-as-draft-only"

    lines = [f"# Purchase Order draft — {status.upper()}", ""]
    lines.append(
        f"**Supplier:** {header.get('supplier', '?')}  |  "
        f"**Company:** {header.get('company', '?')}  |  "
        f"**Date:** {header.get('transaction_date', '?')}  |  "
        f"**Currency:** {header.get('currency', '?')}"
    )
    lines.append("")
    lines.append("## Items")
    lines.append("")
    lines.append("| Item | Qty | Rate | Amount | Warehouse |")
    lines.append("| --- | ---: | ---: | ---: | --- |")
    for item in items:
        qty = item.get("qty") or 0
        rate = item.get("rate") or 0
        amt = qty * rate if isinstance(qty, (int, float)) and isinstance(rate, (int, float)) else 0
        lines.append(
            f"| {item.get('item_code', '?')} | {_fmt(qty)} | {_fmt(rate)} | {_fmt(amt)} | "
            f"{item.get('warehouse') or '*(none)*'} |"
        )
    lines.append(f"| **Total** | | | **{_fmt(total)}** | |")
    lines.append("")

    if problems:
        lines.append(f"## INCOMPLETE — {len(problems)} issue(s)")
        lines.append("")
        for p in problems:
            lines.append(f"- {p}")
        lines.append("")
    else:
        lines.append("## Recommended action")
        lines.append("")
        if recommended_action == "create-as-draft-only":
            lines.append(
                "**Create as draft only** (`docstatus 0`) — PO-submission authority for "
                "the acting user was not confirmed (no Workflow found configured for "
                "Purchase Order on this instance; role membership alone — Purchase User "
                "vs Purchase Manager/Purchase Master Manager — is a heuristic, not a "
                "guarantee). Ask the user to confirm submission authority explicitly, or "
                "leave the PO as a draft for a Purchase Manager to submit themselves."
            )
        else:
            lines.append(
                "**Create, then submit on explicit confirm** — submission authority was "
                "confirmed by the calling skill. Still requires the standard Confirm "
                "step before either the create or the submit call — never chain them "
                "without a human turn in between."
            )
        lines.append("")

    return {
        "status": status,
        "recommended_action": recommended_action,
        "total": total,
        "markdown": "\n".join(lines),
        "problems": problems,
    }


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_po_draft.py <path-to-json-input>", file=sys.stderr)
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

    stock_items = set(payload["stock_items"]) if "stock_items" in payload else None
    result = render_po_draft(
        header=payload.get("header", {}),
        items=payload.get("items", []),
        stock_items=stock_items,
        submission_authority_confirmed=payload.get("submission_authority_confirmed", False),
    )

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(result["markdown"])

    sys.exit(0 if result["status"] == "ready" else 1)


if __name__ == "__main__":
    _cli()
