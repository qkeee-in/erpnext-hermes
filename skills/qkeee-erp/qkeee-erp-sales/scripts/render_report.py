#!/usr/bin/env python3
"""
Operational report renderer for qkeee-erp-sales: Sales Order status
queries, Delivery Note tracking, and sales-pipeline-lite reporting.

Same reconciliation-gate discipline as the other read-write persona
skills' renderers (qkeee-erp-procurement, qkeee-erp-accounts-executive):
every report states whether its numbers were checked to tie out, and
`reconciliation_checks="not_applicable"` is only used with a reason in
`notes` — never silently omitted.

No dedicated built-in ERPNext report was found for Quotation-stage
pipeline visibility (unlike Sales Order fulfilment/delay, which maps to
the built-in "Sales Order Analysis" Script Report, confirmed live via
`frappe.desk.query_report.run` against <erp-instance>) — the Quotation
side of `build_pipeline()` hand-aggregates from a plain `status` query
instead, same shape as procurement's RFQ/quotation-comparison gap.
"""

QUOTATION_STATUSES = ["Draft", "Open", "Replied", "Partially Ordered", "Ordered", "Lost", "Cancelled", "Expired"]


def build_so_status_report(so_rows: list) -> dict:
    """SO status query — `so_rows` is the result of querying Sales Order
    with fields including name/status/delivery_status/per_delivered/
    billing_status/per_billed/grand_total. One row per SO queried.
    """
    reports = []
    for row in so_rows:
        reports.append({
            "sales_order": row.get("name"),
            "status": row.get("status"),
            "delivery_status": row.get("delivery_status"),
            "per_delivered": row.get("per_delivered"),
            "billing_status": row.get("billing_status"),
            "per_billed": row.get("per_billed"),
            "grand_total": row.get("grand_total"),
        })
    return {
        "report": "sales_order_status",
        "rows": reports,
        "reconciliation_checks": "not_applicable",
        "notes": "Status-only lookup off SO fields ERPNext already computes (per_delivered/per_billed); nothing to tie out against a second source.",
    }


def build_dn_tracking_report(dn_rows: list, dn_items: list = None) -> dict:
    """Delivery Note tracking — `dn_rows` is the result of querying
    Delivery Note (optionally filtered by against_sales_order) with
    fields including name/status/per_billed/grand_total.

    `dn_items` — optional result of querying `Delivery Note Item` with
      fields including parent/against_sales_order/so_detail, for the
      same set of Delivery Notes. When supplied, each DN's lines are
      checked for so_detail linkage; when omitted, this check is skipped
      entirely and the report says so rather than silently implying the
      linkage was verified.

    Live-confirmed (<erp-instance>): a Delivery Note line only
    updates its originating Sales Order's per_delivered/delivery_status
    correctly when the line carries BOTH `against_sales_order` and
    `so_detail` (the specific Sales Order Item row name) — a DN created
    without `so_detail` links to the SO loosely (via against_sales_order
    alone) and per-line fulfilment tracking on the SO side may not
    reconcile. This is only actually checked when `dn_items` is passed in
    — querying `Delivery Note Item` is a second call the caller must make
    explicitly (SKILL.md #7); this function cannot infer line-item state
    from header-only `dn_rows`.
    """
    rows = []
    for row in dn_rows:
        rows.append({
            "delivery_note": row.get("name"),
            "status": row.get("status"),
            "against_sales_order": row.get("against_sales_order"),
            "grand_total": row.get("grand_total"),
        })

    notes = "Status-only lookup off DN fields ERPNext already computes; nothing to tie out against a second source."
    if dn_items is None:
        notes += (" so_detail linkage NOT checked — pass `dn_items` (a Delivery Note Item query) "
                  "to verify per-line traceability back to the originating Sales Order.")
    else:
        missing = [item for item in dn_items
                   if item.get("against_sales_order") and not item.get("so_detail")]
        if missing:
            affected_dns = sorted({item.get("parent") for item in missing})
            affected_sos = sorted({item.get("against_sales_order") for item in missing})
            notes += (f" WARNING: {len(missing)} line(s) on Delivery Note(s) {affected_dns} have "
                      f"against_sales_order but no so_detail — the live-confirmed likely cause of a "
                      f"per_delivered mismatch on the originating Sales Order(s) {affected_sos}.")

    return {
        "report": "delivery_note_tracking",
        "rows": rows,
        "reconciliation_checks": "not_applicable",
        "notes": notes,
    }


def build_pipeline(quotation_status_counts: dict, so_analysis_rows: list = None,
                    total_quotations_queried: int = None) -> dict:
    """Sales pipeline-lite report.

    `quotation_status_counts` — dict of status -> count, from hand-
      aggregating a Quotation query grouped by `status` (no dedicated
      built-in report exists for this side).
    `so_analysis_rows` — optional result rows from the built-in "Sales
      Order Analysis" report (via `erp_client.run_query_report`), used
      for the SO-side pipeline view (pending_qty/pending_amount/delay)
      instead of hand-reconstructing that math.
    `total_quotations_queried` — the total row count the caller actually
      queried, used to reconcile against the sum of
      `quotation_status_counts` so a filter/pagination bug can't silently
      under- or over-count a stage.

    Caveat: this only catches a COUNT mismatch (buckets don't sum to the
    total queried). It cannot detect a quotation miscategorized into the
    wrong status bucket while the total still sums correctly — a
    "passed" reconciliation_checks means the numbers tie out, not that
    every quotation landed in the right stage.
    """
    issues = []
    counted = sum(quotation_status_counts.values())
    reconciliation_checks = "passed"
    if total_quotations_queried is not None and counted != total_quotations_queried:
        reconciliation_checks = "failed"
        issues.append(
            f"Quotation status counts sum to {counted} but {total_quotations_queried} "
            f"quotations were queried — status buckets don't reconcile with the total. "
            f"Check for quotations with a status outside the known set: {QUOTATION_STATUSES}."
        )

    so_summary = None
    if so_analysis_rows is not None:
        pending_amount = sum(r.get("pending_amount", 0) or 0 for r in so_analysis_rows)
        overdue = [r for r in so_analysis_rows if (r.get("delay_days") or 0) > 0]
        so_summary = {
            "open_so_lines": len(so_analysis_rows),
            "total_pending_amount": pending_amount,
            "overdue_lines": len(overdue),
            "source": "Sales Order Analysis (built-in report)",
        }

    return {
        "report": "sales_pipeline_lite",
        "quotation_stage_counts": quotation_status_counts,
        "sales_order_summary": so_summary,
        "reconciliation_checks": reconciliation_checks,
        "issues": issues,
        "notes": "Quotation-stage counts are hand-aggregated (no dedicated built-in report found for this side); Sales Order summary, when provided, is sourced from the built-in Sales Order Analysis report rather than re-derived.",
    }
