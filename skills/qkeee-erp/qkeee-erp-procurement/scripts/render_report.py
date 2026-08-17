#!/usr/bin/env python3
"""
qkeee-erp-procurement — operational report renderer (PO status, GRN
matching, RFQ/Supplier Quotation comparison, supplier scorecard query).

Same reconciliation-gate discipline as qkeee-erp-accounts-executive's and
qkeee-erp-mis-analyst's render_report.py: refuses to render without either
a declared, well-formed reconciliation check or an explicit
`reconciliation_checks="not_applicable"` opt-out with a reason (e.g. a
PO status lookup or a quotation comparison has nothing numeric to tie
out). A failed check is surfaced prominently, never hidden.
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
    scope: free-text context line (e.g. "Supplier: Mauli Tea | PO:
      PUR-ORD-2026-00007" or "RFQ: PUR-RFQ-2026-00003, 3 suppliers
      quoted"), same role as accounts-executive's period/company line.
    sections: list of {"title": str, "rows": [{"label": str, "value": num
      or str}], "total": optional {"label": str, "value": num}}. Row
      values may be non-numeric (e.g. a status string) — only numeric
      rows and reconciliation checks are total-checked.
    reconciliation_checks: a non-empty list of well-formed check dicts
      (check/expected/actual/ties_out, numeric expected/actual), or the
      literal string "not_applicable" for a report with genuinely
      nothing to tie out (e.g. a plain status lookup or discrepancy
      list, not a total) — must carry a reason in notes when used.
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


def build_quotation_coverage(all_item_codes: list, supplier_quoted_items: dict) -> dict:
    """RFQ/Supplier Quotation coverage check — code-enforced, not left to
    the calling skill to remember at prompt time.

    all_item_codes: item_codes invited for quote on the RFQ.
    supplier_quoted_items: {supplier: [item_code, ...]} — items each
      supplier actually quoted.

    Returns {supplier: {"quoted": int, "of": int, "complete": bool,
                         "missing": [item_code, ...]}}.
    A supplier who quoted 3 of 5 items is "complete": False with
    "missing" naming exactly which items weren't quoted — the calling
    skill must surface this before ranking on price, per domain-
    knowledge.md's "coverage first" rule.
    """
    all_set = set(all_item_codes)
    coverage = {}
    for supplier, quoted in supplier_quoted_items.items():
        quoted_set = set(quoted)
        missing = sorted(all_set - quoted_set)
        coverage[supplier] = {
            "quoted": len(quoted_set & all_set),
            "of": len(all_set),
            "complete": not missing,
            "missing": missing,
        }
    return coverage


def build_grn_match(po_items: list, receipt_items: list) -> dict:
    """PO -> Purchase Receipt line-level match — code-enforced, not left
    to the calling skill to remember at prompt time.

    po_items: [{"item_code": str, "po_detail": str, "qty": num}, ...] —
      one entry per PO line (po_detail is that line's own `name`).
    receipt_items: [{"purchase_order_item": str, "received_qty": num,
                      "rejected_qty": num}, ...] — Purchase Receipt Item
      rows; purchase_order_item links back to the originating PO line's
      `po_detail`. Multiple receipt rows may reference the same PO line
      (partial receipts across more than one Purchase Receipt).

    Returns {"discrepancies": [...], "clean": bool}. Walks every PO
    line, not just the first mismatch. Quantity and rejected-quantity
    are reported as separate issues on the same line when both apply —
    an item can be "received in full but partly rejected", which is not
    the same fact as "under-received" (domain-knowledge.md).
    """
    receipts_by_po_line = {}
    for r in receipt_items:
        key = r.get("purchase_order_item")
        receipts_by_po_line.setdefault(key, []).append(r)

    discrepancies = []
    for po_item in po_items:
        key = po_item.get("po_detail")
        ordered_qty = po_item.get("qty", 0) or 0
        item_code = po_item.get("item_code", "?")
        matches = receipts_by_po_line.get(key, [])

        if not matches:
            discrepancies.append({
                "item_code": item_code, "issue": "not_received",
                "ordered_qty": ordered_qty, "received_qty": 0, "rejected_qty": 0,
            })
            continue

        received_total = sum(m.get("received_qty", 0) or 0 for m in matches)
        rejected_total = sum(m.get("rejected_qty", 0) or 0 for m in matches)

        if received_total < ordered_qty:
            discrepancies.append({
                "item_code": item_code, "issue": "partial_receipt",
                "ordered_qty": ordered_qty, "received_qty": received_total,
                "rejected_qty": rejected_total,
            })
        elif received_total > ordered_qty:
            discrepancies.append({
                "item_code": item_code, "issue": "over_receipt",
                "ordered_qty": ordered_qty, "received_qty": received_total,
                "rejected_qty": rejected_total,
            })

        if rejected_total:
            # Reported even when received_total == ordered_qty: a fully
            # received-then-partly-rejected line is its own supplier-
            # quality signal, not folded into the quantity check above.
            discrepancies.append({
                "item_code": item_code, "issue": "rejected_quantity",
                "ordered_qty": ordered_qty, "received_qty": received_total,
                "rejected_qty": rejected_total,
            })

    return {"discrepancies": discrepancies, "clean": not discrepancies}


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
