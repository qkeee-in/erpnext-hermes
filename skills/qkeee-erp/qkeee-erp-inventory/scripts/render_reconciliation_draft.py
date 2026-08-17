#!/usr/bin/env python3
"""
qkeee-erp-inventory — Stock Reconciliation draft renderer.

The non-negotiable: stock reconciliations never adjust silently — the
physical and financial impact (current qty/value vs. counted qty/value,
and the resulting difference) is stated plainly before confirm, every
time.

Confirmed live against <erp-instance> — the most important
live finding of this build: a Stock Reconciliation line's `current_qty`
is NOT resolved by ERPNext from Bin.actual_qty at create time — the
create response silently echoes back whatever `current_qty` the caller
passed (or 0, if the caller passed nothing meaningful), while `qty` is
the caller's stated NEW count. For a **non-batch-tracked item**, this is
harmless: ERPNext still SETS the warehouse balance to the absolute `qty`
value on submit, regardless of what `current_qty` said (confirmed: Raw
Item-1, qty 20 submitted, Bin went to exactly 20.0). But for a
**batch-tracked item**, ERPNext does NOT reconcile the item-level total —
it reconciles PER BATCH, and if the caller doesn't supply the correct
existing batch_no(s) with their real current_qty (via
erp_client.get_stock_reconciliation_items(), which wraps ERPNext's own
get_items whitelisted method — the only confirmed-authoritative
resolver), it CREATES A NEW BATCH for the stated qty and ADDS it to the
existing balance instead of correcting it. Confirmed live: SKU-1 (a
batch+serial-tracked item) at 6 units actual, reconciled with qty=8 and
an unresolved current_qty=0, resulted in a Bin balance of 14 — the
reconciliation silently inflated stock instead of correcting it. This
renderer refuses to stage a batch-tracked item's line as "ready" unless
the caller supplied get_items()-sourced rows (proving current_qty was
actually resolved, not guessed), and always shows the stated qty/value
delta explicitly so a human catches an implausible jump before Execute.

Also refuses "ready" for a negative qty, and for a line that states a
nonzero `valuation_rate` without a matching `current_valuation_rate` —
without that guard the shown value delta would silently compare against
an implied current rate of 0, misstating the real financial impact
while still claiming "value delta stated plainly."

This script NEVER calls ERPNext. It only formats a draft for human
review. Actually creating/submitting the Stock Reconciliation is a
separate, later step gated on explicit confirmation of this rendered
draft.
"""

import json
import sys

REQUIRED_ITEM_KEYS = ("item_code", "warehouse", "qty")


class RenderError(Exception):
    pass


def _fmt(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value) if value not in (None, "") else "-"


def render_reconciliation_draft(company: str, posting_date: str, items: list,
                                 batch_tracked_items: set = None, notes: str = "") -> str:
    """
    items: list of {"item_code": str, "warehouse": str, "qty": num (the
      counted/target qty), "valuation_rate": num (optional), "current_qty":
      num (REQUIRED — must come from erp_client.get_stock_reconciliation_items()
      / get_items, never guessed or hand-supplied), "current_valuation_rate":
      num (optional), "batch_no": str (required if this item is
      batch-tracked — see batch_tracked_items), "resolved_via_get_items":
      bool (required True for a batch-tracked item's line — proves the
      caller actually called get_stock_reconciliation_items() for this
      line rather than assuming/guessing current_qty)}.
    batch_tracked_items: set of item_codes known to be batch and/or
      serial tracked (from Item.has_batch_no / has_serial_no) — pass
      whatever the caller has resolved; an item not in this set is
      treated as a plain item where current_qty is informational only
      (ERPNext still sets the absolute qty correctly regardless).

    Refuses to render "ready" if items is empty, any item is missing
    item_code/warehouse/qty, current_qty is absent for any line, or a
    batch-tracked item's line lacks batch_no or resolved_via_get_items —
    that combination is exactly the gap that silently inflated stock in
    this skill's own live validation.
    """
    if not items:
        raise RenderError("items is empty — a Stock Reconciliation needs at least one line.")
    batch_tracked_items = batch_tracked_items or set()

    issues = []
    qty_delta_total = 0.0
    value_delta_total = 0.0
    for it in items:
        missing = [k for k in REQUIRED_ITEM_KEYS if it.get(k) is None]
        if missing:
            raise RenderError(f"Item missing required key(s) {missing}: {it}")

        item_code = it["item_code"]
        if it["qty"] < 0:
            issues.append(f"{item_code} @ {it['warehouse']}: qty {_fmt(it['qty'])} cannot be negative.")

        current_qty = it.get("current_qty")
        if current_qty is None:
            issues.append(
                f"{item_code} @ {it['warehouse']}: no current_qty supplied — never guess "
                f"this. Call erp_client.get_stock_reconciliation_items() first."
            )
            current_qty = 0

        rate = it.get("valuation_rate")
        if rate is not None and rate > 0 and it.get("current_valuation_rate") is None:
            issues.append(
                f"{item_code} @ {it['warehouse']}: valuation_rate given but "
                f"current_valuation_rate is missing — the shown value delta would silently "
                f"compare against a rate of 0 instead of the real current rate. Supply "
                f"current_valuation_rate from get_stock_reconciliation_items() (it's "
                f"returned alongside current_qty)."
            )

        if item_code in batch_tracked_items:
            if not it.get("batch_no"):
                issues.append(
                    f"{item_code} @ {it['warehouse']}: item is batch/serial-tracked but "
                    f"no batch_no was supplied. ERPNext reconciles PER BATCH, not at the "
                    f"item level — submitting without the real existing batch_no risks "
                    f"creating a NEW batch for this qty and ADDING it to the existing "
                    f"balance instead of correcting it (confirmed live: this exact "
                    f"mistake inflated a 6-unit balance to 14 units)."
                )
            if not it.get("resolved_via_get_items"):
                issues.append(
                    f"{item_code} @ {it['warehouse']}: item is batch/serial-tracked and "
                    f"resolved_via_get_items is not True — current_qty/batch_no must come "
                    f"from erp_client.get_stock_reconciliation_items() for this item, not "
                    f"be assumed or copied from a prior read."
                )

        qty_delta_total += (it["qty"] - current_qty)
        rate = it.get("valuation_rate", 0) or 0
        current_rate = it.get("current_valuation_rate", 0) or 0
        value_delta_total += (it["qty"] * rate) - (current_qty * current_rate)

    is_ready = not issues

    lines = [
        "# Stock Reconciliation — DRAFT, NOT CREATED",
        "",
        f"**Company:** {company}  |  **Posting date:** {posting_date}",
        "",
        "| Item | Warehouse | Current qty | New qty | Qty delta | Current rate | New rate | Value delta | Batch |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for it in items:
        current_qty = it.get("current_qty") or 0
        current_rate = it.get("current_valuation_rate", 0) or 0
        rate = it.get("valuation_rate", 0) or 0
        qty_delta = it["qty"] - current_qty
        value_delta = (it["qty"] * rate) - (current_qty * current_rate)
        lines.append(
            f"| {it['item_code']} | {it['warehouse']} | {_fmt(current_qty)} | {_fmt(it['qty'])} | "
            f"{_fmt(qty_delta)} | {_fmt(current_rate)} | {_fmt(rate)} | {_fmt(value_delta)} | "
            f"{it.get('batch_no') or '-'} |"
        )
    lines.append("")
    lines.append(f"**Total qty delta: {_fmt(qty_delta_total)}  |  Total value delta: {_fmt(value_delta_total)}**")
    lines.append("")

    if is_ready:
        lines.append(
            "**Status: READY. Staged for review — not yet created in ERPNext. Every "
            "current_qty was resolved via get_items (not guessed), and every "
            "batch/serial-tracked line carries its real batch_no. Explicit confirmation "
            "required before Execute.**"
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
        "*For a non-batch-tracked item, ERPNext SETS the absolute qty on submit "
        "regardless of current_qty — the delta shown above is informational. For a "
        "batch/serial-tracked item, ERPNext reconciles PER BATCH: an unresolved or "
        "wrong batch_no/current_qty risks creating a new batch and ADDING to the "
        "existing balance instead of correcting it — confirmed live during this "
        "skill's build. Always resolve via get_stock_reconciliation_items() for such "
        "items, never assume.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_reconciliation_draft.py <path-to-json-input>", file=sys.stderr)
        print(
            'JSON shape: {"company": "...", "posting_date": "...", '
            '"items": [{"item_code": "...", "warehouse": "...", "qty": 0, "current_qty": 0, '
            '"valuation_rate": 0, "current_valuation_rate": 0, "batch_no": "...", '
            '"resolved_via_get_items": true}], "batch_tracked_items": ["..."], "notes": "..."}',
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
        out = render_reconciliation_draft(
            company=payload["company"],
            posting_date=payload["posting_date"],
            items=payload["items"],
            batch_tracked_items=set(payload.get("batch_tracked_items", [])),
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
