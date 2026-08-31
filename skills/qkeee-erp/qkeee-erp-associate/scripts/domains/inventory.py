#!/usr/bin/env python3
"""
qkeee-erp-associate — inventory domain (Stock, transfers, reconciliation).

Ported from qkeee-erp-inventory/scripts/erp_client.py during Phase 1
(connector consolidation). Unlike accounts/hr_payroll/sales/procurement,
this skill's erp_client.py DID carry genuine business logic beyond the
shared core set: get_stock_reconciliation_items(), bin_rows_to_actual_source_qty(),
and get_bin_qty() below, all ported verbatim (see the docstrings for the
two live-bug-driven code gates noted in the plan's section 1 table — the
batch-tracked-item Stock Reconciliation footgun this trio exists to
prevent).

ALLOWED_WRITE_DOCTYPES is a first-pass allowlist inferred from
render_stock_entry_draft.py / render_material_request_draft.py /
render_reconciliation_draft.py's target doctypes. Confirm/expand against
references/domains/inventory.md once authored (Phase 2).
"""

import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client
from core.client import _request, get_env_config

DOMAIN_NAME = "inventory"

ALLOWED_WRITE_DOCTYPES = (
    "Stock Entry",
    "Material Request",
    "Stock Reconciliation",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """This domain's write entry point — plain mutate_resource() gated by
    ALLOWED_WRITE_DOCTYPES above (domain="inventory")."""
    return core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)


def get_stock_reconciliation_items(tag: str, warehouse: str, company: str,
                                    posting_date: str, item_code: str = None,
                                    posting_time: str = "23:59:59") -> dict:
    """Resolve authoritative current qty/valuation (and, for batch-tracked
    items, per-batch rows) for a warehouse/item via ERPNext's own
    get_items whitelisted method — NEVER guess or hand-supply current_qty
    for a Stock Reconciliation line.

    Confirmed live: the Stock Reconciliation Item's `current_qty` field is
    NOT resolved server-side from a caller-supplied value at create time
    (it's silently reset to 0 in the create response regardless of what's
    passed in), and Bin.actual_qty is NOT authoritative for a batch-tracked
    item — get_items() is the only confirmed-correct source, and for a
    batch-tracked item it returns one row PER EXISTING BATCH (not a single
    item-level total), each with its own batch_no + current_qty. A caller
    that reconciles a batch-tracked item without resolving per-batch rows
    first and instead submits a single line with an unresolved/zero
    current_qty risks ERPNext creating a brand-new batch for the delta
    instead of correcting existing batches — confirmed live: this exact
    mistake inflated a 6-unit balance to 14 units (should have been
    corrected to 8) because the reconcile silently ADDED 8 to the existing
    balance rather than SETTING it, when current_qty was passed as
    0/unresolved for a batch-tracked item.

    `item_code` is optional (confirmed live): omitting it returns every
    item with a nonzero/tracked balance in `warehouse`, not just one —
    useful for reconciling a whole warehouse's physical count in one
    resolver call instead of one per item.
    """
    cfg = get_env_config(tag)
    payload = {
        "warehouse": warehouse,
        "posting_date": posting_date,
        "posting_time": posting_time,
        "company": company,
    }
    if item_code:
        payload["item_code"] = item_code
    result = _request(
        cfg, "POST",
        "/api/method/erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_items",
        payload=payload,
    )
    rows = result.get("message", [])
    return {"item_code": item_code, "warehouse": warehouse, "rows": rows, "batch_tracked": any(r.get("batch_no") for r in rows)}


def bin_rows_to_actual_source_qty(bin_rows: list) -> dict:
    """Convert get_bin_qty()'s ``{"data": [{"item_code", "warehouse",
    "actual_qty"}, ...]}`` row shape into the ``{(item_code, warehouse):
    qty}`` mapping a stock-entry draft renderer expects as
    `actual_source_qty`.

    Added because this translation was previously left undocumented and
    untested, entirely up to whatever called get_bin_qty() to get the
    shape right at runtime — a caller passing get_bin_qty()'s raw ``data``
    list straight into actual_source_qty would fail every availability
    check with "no actual_source_qty entry provided", not because stock
    was actually unavailable but because the shapes never matched.
    """
    return {(row["item_code"], row["warehouse"]): row["actual_qty"] for row in bin_rows}


def get_bin_qty(tag: str, item_code: str, warehouse: str = None) -> dict:
    """Read the live Bin (actual on-hand qty) for an item, optionally
    scoped to one warehouse. Authoritative for non-batch/non-serial items;
    for batch-tracked items prefer get_stock_reconciliation_items() to
    also see the per-batch breakdown before staging a reconciliation.
    """
    cfg = get_env_config(tag)
    filters = [["item_code", "=", item_code]]
    if warehouse:
        filters.append(["warehouse", "=", warehouse])
    params = {"filters": json.dumps(filters), "fields": json.dumps(["item_code", "warehouse", "actual_qty"]),
              "limit_page_length": 100}
    result = _request(cfg, "GET", "/api/resource/Bin", params=params)
    return {"data": result.get("data", [])}
