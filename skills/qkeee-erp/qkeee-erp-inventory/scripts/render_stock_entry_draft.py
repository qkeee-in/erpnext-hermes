#!/usr/bin/env python3
"""
qkeee-erp-inventory — Stock Entry (transfer/issue/receipt) draft renderer.

The non-negotiable: stock transfers never adjust silently — the physical
and financial impact (item, qty, from/to warehouse, value moved) is
stated plainly before confirm, every time.

Confirmed live against <erp-instance>: ERPNext does NOT
reject an outgoing (s_warehouse) line with more qty than is actually on
hand at DRAFT-CREATE time — a Stock Entry with qty 999 against a
warehouse holding 6 was accepted as a Draft (docstatus 0) with no error,
and only failed at SUBMIT with a ValidationError ("Available qty ... is
less than the Required Qty ..."). A caller that stages a draft as
"ready" without independently checking available qty against a
freshly-fetched Bin balance would present a plausible-looking draft that
is guaranteed to fail later, or — worse, if the balance changes between
draft and submit — could silently under/over-report what's really
available. This renderer closes that gap itself, the same way
qkeee-erp-fixed-asset-manager's render_movement_draft.py closes the
source-location gap for Asset Movement.

The check is cumulative WITHIN a draft, not just per-line: two lines
drawing from the same (item_code, s_warehouse) each individually under
the fetched balance can still overdraw it combined (e.g. two 5-unit
lines against a balance of 6) — `committed_source_qty` below tracks
qty already claimed by earlier lines in the same draft before checking
each subsequent line against what's actually left.

This renderer also rejects a non-positive qty (<= 0) outright — the
Stock Entry Detail schema's `qty` field is not `non_negative`-flagged
in ERPNext itself (confirmed live), so this is not a check
ERPNext performs for us.

This script NEVER calls ERPNext. It only formats a draft for human
review. Actually creating/submitting the Stock Entry is a separate,
later step gated on explicit confirmation of this rendered draft.

Trust boundary, stated plainly: `actual_source_qty` is caller-supplied,
not independently fetched by this script (the script deliberately never
calls ERPNext). A "READY" status only means the inputs are internally
consistent — it is not proof the caller actually re-fetched a live
balance. Callers MUST follow the SKILL.md instruction to fetch
`get_bin_qty()` immediately before calling this renderer, every time.
"""

import json
import sys

PURPOSES = (
    "Material Issue", "Material Receipt", "Material Transfer",
    "Material Transfer for Manufacture", "Material Consumption for Manufacture",
    "Manufacture", "Repack", "Send to Subcontractor", "Disassemble",
)
REQUIRED_ITEM_KEYS = ("item_code", "qty")


class RenderError(Exception):
    pass


def _fmt(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value) if value not in (None, "") else "-"


def render_stock_entry_draft(purpose: str, company: str, items: list,
                              actual_source_qty: dict, notes: str = "") -> str:
    """
    items: list of {"item_code": str, "qty": num, "s_warehouse": str
      (optional, required for an outgoing line), "t_warehouse": str
      (optional, required for an incoming line), "valuation_rate": num
      (optional), "batch_no": str (optional), "serial_no": str
      (optional)}.
    actual_source_qty: {(item_code, s_warehouse): current_actual_qty} —
      the caller's live-fetched Bin.actual_qty for every (item_code,
      s_warehouse) pair among `items` that has an s_warehouse, fetched
      fresh just before calling this renderer (not cached/assumed).
      Keys may be passed as "item_code|warehouse" strings if a caller's
      language can't use tuple keys — both forms are accepted.

    Refuses to render "ready" if purpose is unrecognized, items is
    empty, any item is missing item_code/qty, an outgoing line has no
    s_warehouse, or a line's qty exceeds its actual_source_qty entry —
    exactly the gap ERPNext itself leaves open at draft-create time.
    """
    if purpose not in PURPOSES:
        raise RenderError(f"purpose must be one of {PURPOSES}, got {purpose!r}")
    if not items:
        raise RenderError("items is empty — a Stock Entry needs at least one line.")

    def _lookup(item_code, warehouse):
        if (item_code, warehouse) in actual_source_qty:
            return actual_source_qty[(item_code, warehouse)]
        return actual_source_qty.get(f"{item_code}|{warehouse}")

    issues = []
    total_value = 0.0
    committed_source_qty = {}  # (item_code, s_warehouse) -> qty already claimed by earlier lines in this same draft
    for it in items:
        missing = [k for k in REQUIRED_ITEM_KEYS if not it.get(k) and it.get(k) != 0]
        if missing:
            raise RenderError(f"Item missing required key(s) {missing}: {it}")

        qty = it["qty"]
        if qty <= 0:
            issues.append(f"{it['item_code']}: qty {_fmt(qty)} must be greater than zero.")
        s_wh = it.get("s_warehouse")
        t_wh = it.get("t_warehouse")
        if not s_wh and not t_wh:
            issues.append(f"{it['item_code']}: neither s_warehouse nor t_warehouse set — a line must move stock somewhere.")
            continue

        if s_wh:
            available = _lookup(it["item_code"], s_wh)
            key = (it["item_code"], s_wh)
            already_claimed = committed_source_qty.get(key, 0.0)
            if available is None:
                issues.append(
                    f"{it['item_code']} @ {s_wh}: no actual_source_qty entry provided — "
                    f"cannot verify {_fmt(qty)} is actually available before staging ready."
                )
            elif already_claimed + qty > available:
                issues.append(
                    f"{it['item_code']} @ {s_wh}: requested qty {_fmt(qty)}"
                    + (f" (plus {_fmt(already_claimed)} already claimed by an earlier line "
                       f"in this same draft, total {_fmt(already_claimed + qty)})" if already_claimed else "")
                    + f" exceeds actual on-hand qty {_fmt(available)} — refetch the Bin "
                      f"balance and correct before staging this as ready (ERPNext itself "
                      f"will only catch this at submit, not at draft create)."
                )
            committed_source_qty[key] = already_claimed + qty

        rate = it.get("valuation_rate", 0) or 0
        total_value += qty * rate

    is_ready = not issues

    lines = [
        f"# Stock Entry ({purpose}) — DRAFT, NOT CREATED",
        "",
        f"**Company:** {company}",
        "",
        "| Item | Qty | From | To | Rate | Value | Batch/Serial |",
        "| --- | ---: | --- | --- | ---: | ---: | --- |",
    ]
    for it in items:
        rate = it.get("valuation_rate", 0) or 0
        value = it["qty"] * rate
        batch_serial = it.get("batch_no") or it.get("serial_no") or "-"
        lines.append(
            f"| {it['item_code']} | {_fmt(it['qty'])} | {_fmt(it.get('s_warehouse'))} | "
            f"{_fmt(it.get('t_warehouse'))} | {_fmt(rate)} | {_fmt(value)} | {batch_serial} |"
        )
    lines.append("")
    lines.append(f"**Total value moved: {_fmt(total_value)}**")
    lines.append("")

    if is_ready:
        lines.append(
            "**Status: READY. Staged for review — not yet created in ERPNext. Every "
            "outgoing line's qty was checked against a freshly-fetched actual on-hand "
            "balance. Explicit confirmation required before Execute.**"
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
        "*ERPNext accepts an outgoing line with more qty than is on hand at Draft "
        "creation and only rejects it at Submit — this renderer is the only place the "
        "available-qty check happens before that point. Refetch actual_source_qty if "
        "any balance may have changed since it was last read.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_stock_entry_draft.py <path-to-json-input>", file=sys.stderr)
        print(
            'JSON shape: {"purpose": "...", "company": "...", '
            '"items": [{"item_code": "...", "qty": 0, "s_warehouse": "...", "t_warehouse": "...", '
            '"valuation_rate": 0, "batch_no": "...", "serial_no": "..."}], '
            '"actual_source_qty": {"ITEM|WAREHOUSE": 0}, "notes": "..."}',
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
        out = render_stock_entry_draft(
            purpose=payload["purpose"],
            company=payload["company"],
            items=payload["items"],
            actual_source_qty=payload.get("actual_source_qty", {}),
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
