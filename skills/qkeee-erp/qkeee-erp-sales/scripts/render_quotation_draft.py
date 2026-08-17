#!/usr/bin/env python3
"""
Quotation draft renderer for qkeee-erp-sales.

Non-negotiable (module plan + this skill's SKILL.md): a Quotation is
drafted, never auto-submitted as a formal customer commitment, without
explicit confirmation. This renderer therefore only ever produces a
create-as-draft (docstatus 0) recommendation — there is no
"submission_authority_confirmed"-style override the way Purchase Order
has one in qkeee-erp-procurement. Submitting an already-created Quotation
draft (docstatus 0 -> 1, status Draft -> Open) is a distinct, separately
confirmed step in SKILL.md's Execute stage, not something this renderer
ever recommends doing in the same call as create.

Live-confirmed findings (<erp-instance>) this renderer encodes:
  - `party_name` (the actual customer link, fieldtype Dynamic Link) is
    NOT flagged `reqd` in the Quotation DocType schema, but is practically
    required: ERPNext will silently create and happily accept a Quotation
    with `quotation_to: "Customer"` and no `party_name` at all — a
    "quotation to nobody" that is never a real business document. This
    renderer requires it explicitly, stricter than ERPNext's own schema.
  - Quotation Item's `item_code` is likewise NOT `reqd` in the schema
    (an `item_name`-only line is schema-valid) but this renderer requires
    it — an item_name-only line silently defaults price_list_rate to 0
    and skips ERPNext's own is_sales_item check entirely, which is worse
    than refusing to draft it.
  - If `item_code` IS given, ERPNext's own validate() rejects the whole
    Quotation if that item is not marked "Sales Item" — confirmed live
    with a specific ValidationError ("... is not marked as sales item").
    This renderer pre-checks the same condition against a caller-supplied
    `sales_items` set (resolved by the calling skill via
    `Item.is_sales_item`, mirroring qkeee-erp-procurement's `stock_items`
    resolution pattern) so the user gets one clear pre-flight message
    instead of ERPNext's HTTP 417.
  - `warehouse` on each Quotation Item auto-defaults from the Item
    master's default warehouse when omitted — unlike Purchase Order,
    there is no live-discovered hidden-mandatory-warehouse trap here.

Also refuses a non-positive qty or a negative rate outright — ERPNext's
own schema doesn't guard against either (confirmed pattern from
qkeee-erp-inventory's equivalent finding; not independently re-verified
against this skill's DocTypes, but the risk of a silently-accepted
negative qty/rate quotation line is the same regardless).

Trust boundary, stated plainly: `sales_items` is caller-supplied, not
independently re-verified by this script (which deliberately never
calls ERPNext). A "ready" result only means the inputs are internally
consistent — it is not proof the caller actually re-queried
Item.is_sales_item fresh. Callers MUST follow the SKILL.md instruction
to resolve it immediately before calling this renderer, every time.
"""

REQUIRED_HEADER_FIELDS = ["quotation_to", "party_name", "transaction_date", "company",
                          "currency", "conversion_rate", "selling_price_list",
                          "price_list_currency", "plc_conversion_rate", "order_type"]


def render_quotation_draft(fields: dict, items: list, sales_items: set = None) -> dict:
    """Build a staged Quotation create-as-draft payload.

    `fields` — Quotation header fields (quotation_to, party_name,
      transaction_date, company, currency, conversion_rate,
      selling_price_list, price_list_currency, plc_conversion_rate,
      order_type, ...).
    `items` — list of {"item_code": ..., "qty": ..., "rate": ...} (rate
      optional — omitting it lets ERPNext price from selling_price_list).
    `sales_items` — set of item_codes confirmed via `Item.is_sales_item`
      to be sales-enabled. If None, every item is flagged as unconfirmed
      rather than silently assumed sales-enabled (mirrors procurement's
      "treat everything as stock" anti-pattern warning, inverted).

    Returns {"ready": bool, "issues": [...], "quotation_payload": {...},
             "recommended_action": "create-as-draft-only"}.
    """
    issues = []

    for field in REQUIRED_HEADER_FIELDS:
        if not fields.get(field):
            issues.append(f"Missing required header field: {field}")

    if not items:
        issues.append("No line items provided.")

    line_payload = []
    for i, item in enumerate(items):
        item_code = item.get("item_code")
        if not item_code:
            issues.append(f"Line {i + 1}: missing item_code (item_name-only lines are refused by this skill).")
            continue
        qty = item.get("qty")
        if not qty:
            issues.append(f"Line {i + 1} ({item_code}): missing qty.")
        elif qty <= 0:
            issues.append(f"Line {i + 1} ({item_code}): qty {qty} must be greater than zero.")
        rate = item.get("rate")
        if rate is not None and rate < 0:
            issues.append(f"Line {i + 1} ({item_code}): rate {rate} cannot be negative.")
        if sales_items is None:
            issues.append(
                f"Line {i + 1} ({item_code}): sales-item status not confirmed — resolve "
                f"Item.is_sales_item and pass it in via `sales_items` before drafting."
            )
        elif item_code not in sales_items:
            issues.append(
                f"Line {i + 1} ({item_code}): not marked as a Sales Item in ERPNext — "
                f"this Quotation would be rejected at create time. Enable it on the Item "
                f"master first, or drop the line."
            )
        line_entry = {"item_code": item_code, "qty": item.get("qty")}
        if item.get("rate") is not None:
            line_entry["rate"] = item["rate"]
        line_payload.append(line_entry)

    quotation_payload = dict(fields)
    quotation_payload["items"] = line_payload

    return {
        "ready": len(issues) == 0,
        "issues": issues,
        "quotation_payload": quotation_payload,
        "recommended_action": "create-as-draft-only",
    }
