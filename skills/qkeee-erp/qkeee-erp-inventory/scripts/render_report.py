#!/usr/bin/env python3
"""
qkeee-erp-inventory — operational report renderer (stock level query,
batch/serial trace).

Same reconciliation-gate discipline as every other qkeee-erp-* skill's
render_report.py: refuses to render without either a declared,
well-formed reconciliation check or an explicit
`reconciliation_checks="not_applicable"` opt-out with a reason. A failed
check is surfaced prominently, never hidden.
"""

import html
import json
import sys

REQUIRED_CHECK_KEYS = ("check", "expected", "actual", "ties_out")
NOT_APPLICABLE = "not_applicable"


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


def _diff(actual, expected) -> str:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) \
            and not isinstance(expected, bool) and not isinstance(actual, bool):
        return _fmt(actual - expected)
    return f"'{actual}' vs expected '{expected}'"


def render_report(title: str, scope: str, sections: list,
                   reconciliation_checks, notes: str = "") -> str:
    """
    scope: free-text context line (e.g. "Item: SKU-1 | Warehouses: Stores
      - QL, Finished Goods - QL" or "Batch: SKU1-00007").
    sections: list of {"title": str, "rows": [{"label": str, "value": num
      or str}], "total": optional {"label": str, "value": num}}.
    reconciliation_checks: a non-empty list of well-formed check dicts
      (check/expected/actual/ties_out, numeric expected/actual), or the
      literal string "not_applicable" for a report with genuinely
      nothing to tie out — must carry a reason in notes when used.
    """
    if reconciliation_checks != NOT_APPLICABLE:
        if not reconciliation_checks:
            raise RenderError(
                "No reconciliation_checks declared. Every report must self-check at "
                "least one tie-out, or explicitly pass reconciliation_checks="
                "'not_applicable' with a reason in notes."
            )
        for c in reconciliation_checks:
            missing = [k for k in REQUIRED_CHECK_KEYS if k not in c]
            if missing:
                raise RenderError(f"Reconciliation check missing required key(s) {missing}: {c}")
            for key in ("expected", "actual"):
                if not isinstance(c[key], (int, float)) or isinstance(c[key], bool):
                    raise RenderError(
                        f"Reconciliation check '{c['check']}' has non-numeric {key}: {c[key]!r}"
                    )
    elif not notes:
        raise RenderError(
            "reconciliation_checks='not_applicable' requires a reason in notes."
        )

    for s in sections:
        for r in s.get("rows", []):
            val = r["value"]
            if isinstance(val, (int, float)) and isinstance(val, bool):
                raise RenderError(f"Row '{r['label']}' in section '{s['title']}' has a boolean value: {val!r}")

    failures = [] if reconciliation_checks == NOT_APPLICABLE else [
        c for c in reconciliation_checks if not c["ties_out"]
    ]

    lines = [f"# {title}", "", f"**Scope:** {scope}", ""]

    if failures:
        lines.append(f"**ANOMALY — {len(failures)} reconciliation check(s) did not tie out:**")
        for c in failures:
            lines.append(
                f"- `{c['check']}`: expected {_fmt(c['expected'])}, got {_fmt(c['actual'])} "
                f"(diff {_diff(c['actual'], c['expected'])})"
            )
        lines.append("")

    for s in sections:
        lines.append(f"## {s['title']}")
        lines.append("")
        lines.append("| | Value |")
        lines.append("| --- | ---: |")
        for r in s.get("rows", []):
            val = r["value"]
            lines.append(f"| {r['label']} | {_fmt(val) if isinstance(val, (int, float)) else val} |")
        if s.get("total"):
            lines.append(f"| **{s['total']['label']}** | **{_fmt(s['total']['value'])}** |")
        lines.append("")

    lines.append("## Reconciliation")
    lines.append("")
    if reconciliation_checks == NOT_APPLICABLE:
        lines.append("*No tie-out check applies to this report* — declared `not_applicable`. See Notes for why.")
    else:
        lines.append("| Check | Expected | Actual | Ties out |")
        lines.append("| --- | ---: | ---: | :---: |")
        for c in reconciliation_checks:
            mark = "yes" if c["ties_out"] else "**NO**"
            lines.append(f"| {c['check']} | {_fmt(c['expected'])} | {_fmt(c['actual'])} | {mark} |")
    lines.append("")

    if notes:
        lines += ["## Notes", "", notes, ""]

    return "\n".join(lines)


def build_stock_level_check(bin_rows: list, expected_total: float) -> dict:
    """Stock level query tie-out — code-enforced, not left to the calling
    skill to re-sum by hand.

    bin_rows: [{"warehouse": str, "actual_qty": num}, ...] — the live
      Bin rows for one item across however many warehouses were queried.
    expected_total: the total the caller intends to present (e.g. sum
      across a specific warehouse subset, or "all warehouses").

    Returns a single reconciliation_checks-shaped dict: sum(bin_rows) vs
    expected_total. Use when expected_total is meant to equal the full
    sum of bin_rows passed in (e.g. "total across queried warehouses");
    if the caller is intentionally presenting a subset, state that in
    notes and use reconciliation_checks="not_applicable" instead of
    calling this.
    """
    actual_total = sum(r.get("actual_qty", 0) or 0 for r in bin_rows)
    return {
        "check": "sum(Bin.actual_qty across queried warehouses) == reported total",
        "expected": expected_total,
        "actual": actual_total,
        "ties_out": abs(actual_total - expected_total) < 1e-6,
    }


def build_batch_serial_trace(ledger_entries: list) -> dict:
    """Batch/serial trace — code-enforced chronological walk, not left to
    the calling skill to re-derive by hand.

    ledger_entries: [{"posting_date": str, "warehouse": str, "actual_qty":
      num, "qty_after_transaction": num, "voucher_type": str,
      "voucher_no": str}, ...] — Stock Ledger Entry rows for one
      item/batch/serial, in the order returned by the query (assumed
      chronological — ERPNext's own posting_date/creation ordering).

    Returns {"events": [...], "running_balance_consistent": bool,
    "final_qty": num}. running_balance_consistent is False if any row's
    qty_after_transaction doesn't equal the prior row's
    qty_after_transaction + this row's actual_qty — a break here means
    either a gap in the queried range (e.g. limit truncation) or a
    genuine ledger anomaly; surface it, don't silently trust the last
    row's balance.
    """
    events = []
    consistent = True
    prior_balance = None
    for e in ledger_entries:
        qty_after = e.get("qty_after_transaction", 0) or 0
        actual_qty = e.get("actual_qty", 0) or 0
        if prior_balance is not None and abs((prior_balance + actual_qty) - qty_after) > 1e-6:
            consistent = False
        events.append({
            "posting_date": e.get("posting_date"),
            "warehouse": e.get("warehouse"),
            "movement": actual_qty,
            "balance_after": qty_after,
            "voucher_type": e.get("voucher_type"),
            "voucher_no": e.get("voucher_no"),
        })
        prior_balance = qty_after

    return {
        "events": events,
        "running_balance_consistent": consistent,
        "final_qty": prior_balance if prior_balance is not None else 0,
    }


def html_wrapper(title: str, markdown_body: str) -> str:
    escaped = html.escape(markdown_body)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        f"<style>body{{font-family:sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}}"
        f"pre{{white-space:pre-wrap}}</style></head><body><pre>{escaped}</pre></body></html>"
    )


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_report.py <path-to-json-input>", file=sys.stderr)
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
        report = render_report(
            title=payload["title"],
            scope=payload["scope"],
            sections=payload.get("sections", []),
            reconciliation_checks=payload["reconciliation_checks"],
            notes=payload.get("notes", ""),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if payload.get("format") == "html":
        report = html_wrapper(payload["title"], report)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(report)


if __name__ == "__main__":
    _cli()
