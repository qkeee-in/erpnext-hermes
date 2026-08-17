#!/usr/bin/env python3
"""
qkeee-erp-fixed-asset-manager — Asset capitalization draft renderer.

Enforces this skill's capitalization bar in code, not just the prompt.
Confirmed live against <erp-instance>: ERPNext's own hard-mandatory
fields on Asset are only company/item_code/asset_name/location/
purchase_date — narrower than what a meticulous asset manager actually
needs before treating a capitalization as "ready":

  - a real value/cost basis (gross_purchase_amount > 0, or an explicit
    stated reason it's zero — e.g. a fully-donated asset)
  - a source: either a linked Purchase Receipt/Invoice (capitalized from
    an actual purchase) or is_existing_asset=1 stated explicitly (an
    opening-balance asset with no ERPNext purchase document behind it) —
    never silently ambiguous about which
  - if calculate_depreciation is set, at least one complete finance book
    (depreciation_method, total_number_of_depreciations,
    frequency_of_depreciation, depreciation_start_date all present) —
    confirmed live: Asset Finance Book's own DocType schema flags
    depreciation_method/total_number_of_depreciations/
    frequency_of_depreciation as reqd; depreciation_start_date is NOT
    schema-flagged reqd but every live-tested schedule generation used
    one — treat it as required here rather than relying on an
    unconfirmed ERPNext default.
  - asset_category set — not schema-mandatory (confirmed live: creation
    succeeds without it), but without it there is no Asset Category
    Account mapping (fixed asset / accumulated depreciation /
    depreciation expense accounts), and a depreciation run has nowhere
    to post to. Refuse "ready" if calculate_depreciation is set and
    asset_category is missing.

This script NEVER calls ERPNext. It only formats a draft for human
review. Actually creating the Asset (via scripts/erp_client.py's
mutate_resource) is a separate, later step gated on explicit user
confirmation of this rendered draft.
"""

import json
import sys

REQUIRED_KEYS = ("asset_name", "item_code", "company", "location", "purchase_date")
REQUIRED_FINANCE_BOOK_KEYS = (
    "depreciation_method",
    "total_number_of_depreciations",
    "frequency_of_depreciation",
    "depreciation_start_date",
)


class RenderError(Exception):
    pass


def _fmt(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value) if value not in (None, "") else "-"


def render_asset_draft(asset: dict, notes: str = "") -> str:
    """
    asset: dict shaped like the payload that would go to
      mutate_resource("Asset", "create", payload=asset). Required keys
      per REQUIRED_KEYS above. Recognizes: gross_purchase_amount,
      is_existing_asset, purchase_receipt, purchase_invoice,
      asset_category, calculate_depreciation, finance_books (list),
      custodian, department, cost_center.

    Returns the rendered draft with a "ready" / "incomplete" verdict
    baked in — never silently marks something ready that fails these
    checks. Raises RenderError only for structurally missing required
    keys (nothing to render at all); completeness gaps below that are
    reported IN the draft as "incomplete", not raised, so the user sees
    exactly what's missing rather than getting a bare error.
    """
    missing_required = [k for k in REQUIRED_KEYS if not asset.get(k)]
    if missing_required:
        raise RenderError(f"Missing required field(s) for a draft at all: {missing_required}")

    gaps = []

    gross_amount = asset.get("gross_purchase_amount")
    if gross_amount is not None and isinstance(gross_amount, (int, float)) \
            and not isinstance(gross_amount, bool) and gross_amount < 0:
        gaps.append(
            f"gross_purchase_amount is negative ({gross_amount!r}) — this looks like a "
            "sign/data-entry error, not a valid cost basis. Correct it before proceeding."
        )
    elif not gross_amount:
        gaps.append(
            "gross_purchase_amount is missing or zero — state a real cost basis, or an "
            "explicit reason it's genuinely zero (e.g. fully donated), in notes."
        )

    has_source_doc = bool(asset.get("purchase_receipt") or asset.get("purchase_invoice"))
    is_existing = bool(asset.get("is_existing_asset"))
    if not has_source_doc and not is_existing:
        gaps.append(
            "No purchase_receipt/purchase_invoice link AND is_existing_asset is not set — "
            "this capitalization's source is ambiguous. State explicitly whether this is "
            "capitalized from an ERPNext purchase document or is an opening-balance "
            "existing asset with no source document."
        )

    if not asset.get("asset_category"):
        gaps.append(
            "asset_category is not set. Not schema-mandatory in ERPNext, but without it "
            "there is no Asset Category Account mapping — a depreciation run for this "
            "asset would have nowhere to post fixed-asset/accumulated-depreciation/"
            "depreciation-expense entries."
        )

    if asset.get("calculate_depreciation"):
        finance_books = asset.get("finance_books") or []
        if not finance_books:
            gaps.append(
                "calculate_depreciation is set but no finance_books entry is present — "
                "a depreciation schedule cannot be generated with no method/term declared."
            )
        else:
            for i, fb in enumerate(finance_books):
                fb_missing = [k for k in REQUIRED_FINANCE_BOOK_KEYS if not fb.get(k)]
                if fb_missing:
                    gaps.append(f"finance_books[{i}] missing {fb_missing}.")

    is_ready = not gaps

    lines = [
        "# Asset capitalization — DRAFT, NOT CREATED",
        "",
        f"**Asset name:** {asset['asset_name']}  |  **Item:** {asset['item_code']}",
        f"**Company:** {asset['company']}  |  **Location:** {asset['location']}",
        f"**Purchase date:** {asset['purchase_date']}  |  "
        f"**Available for use:** {_fmt(asset.get('available_for_use_date'))}",
        f"**Asset category:** {_fmt(asset.get('asset_category'))}  |  "
        f"**Cost basis:** {_fmt(gross_amount)}",
        f"**Source:** "
        + (
            f"Purchase Receipt {asset['purchase_receipt']}" if asset.get("purchase_receipt")
            else f"Purchase Invoice {asset['purchase_invoice']}" if asset.get("purchase_invoice")
            else "existing asset (opening balance, no source document)" if is_existing
            else "UNSTATED"
        ),
        f"**Custodian:** {_fmt(asset.get('custodian'))}  |  "
        f"**Department:** {_fmt(asset.get('department'))}  |  "
        f"**Cost center:** {_fmt(asset.get('cost_center'))}",
        "",
    ]

    if asset.get("calculate_depreciation"):
        lines.append("**Depreciation:** enabled")
        for i, fb in enumerate(asset.get("finance_books") or []):
            lines.append(
                f"  - Book {i}: {_fmt(fb.get('depreciation_method'))}, "
                f"{_fmt(fb.get('total_number_of_depreciations'))} periods, "
                f"every {_fmt(fb.get('frequency_of_depreciation'))} month(s) starting "
                f"{_fmt(fb.get('depreciation_start_date'))}, "
                f"salvage {_fmt(fb.get('expected_value_after_useful_life'))}"
            )
    else:
        lines.append("**Depreciation:** not enabled for this asset.")
    lines.append("")

    if is_ready:
        lines.append(
            "**Status: READY. Staged for review — not yet created in ERPNext. "
            "Explicit confirmation required before Execute.**"
        )
    else:
        lines.append(f"**Status: INCOMPLETE — {len(gaps)} gap(s) below must be resolved first.**")
        lines.append("")
        lines.append("## Gaps")
        for g in gaps:
            lines.append(f"- {g}")
    lines.append("")

    if notes:
        lines += ["## Notes", "", notes, ""]

    lines += [
        "---",
        "*Confirm location, custodian, and asset_category against real ERPNext records "
        "before approving — this renderer enforces completeness of the declared fields, "
        "not that referenced records actually exist.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_asset_draft.py <path-to-json-input>", file=sys.stderr)
        print('JSON shape: {"asset": {...}, "notes": "..."}', file=sys.stderr)
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
        draft = render_asset_draft(asset=payload["asset"], notes=payload.get("notes", ""))
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(draft)


if __name__ == "__main__":
    _cli()
