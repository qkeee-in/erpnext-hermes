#!/usr/bin/env python3
"""
qkeee-erp-fixed-asset-manager — asset disposal/scrap confirmation renderer.

The non-negotiable: disposal never executes without explicit
confirmation, double-confirmed given irreversibility in practice
(status flips to Scrapped/Sold, a Journal Entry or Sales Invoice posts
against the asset's accounts — technically cancelable but never
casually so).

Two disposal methods, confirmed live against <erp-instance>:

  - **Scrap** — erpnext.assets.doctype.asset.depreciation.scrap_asset
    (asset_name). No consideration received; the entire remaining book
    value is written off via an auto-created Journal Entry. Confirmed
    live: `Asset.status` -> "Scrapped", `journal_entry_for_scrap` set,
    a Journal Entry created automatically (no draft/review step of its
    own — the scrap call itself IS the write, so this renderer's
    confirmation is the only human checkpoint before it happens).
  - **Sale** — erpnext.assets.doctype.asset.asset.make_sales_invoice
    (asset, item_code, company) drafts a Sales Invoice representing the
    disposal; gain/loss is realized when that invoice is submitted, a
    separate step this skill does not auto-perform (Sales Invoice
    submission is `qkeee-erp-sales`/`qkeee-erp-accounts-executive`
    territory once drafted — this renderer states the estimated
    gain/loss for the user's confirmation, it doesn't submit anything).

This script NEVER calls ERPNext. It only formats the confirmation.
"""

import json
import sys

from confirm_token import disposal_token

METHODS = ("scrap", "sale")


class RenderError(Exception):
    pass


def _fmt(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value) if value not in (None, "") else "-"


def render_disposal(asset: str, asset_name: str, method: str, disposal_date: str,
                     current_book_value: float, sale_proceeds: float = None,
                     reason: str = "", book_value_source: str = "finance_books",
                     notes: str = "") -> str:
    """
    method: "scrap" (no proceeds, full book value written off) or "sale"
      (proceeds vs. book value realizes a gain/loss). sale_proceeds is
      REQUIRED when method == "sale", ignored otherwise.

    book_value_source: must be the literal "finance_books" — same rule
      as render_depreciation_run.py: current_book_value must come from
      Asset.finance_books[N].value_after_depreciation, never the stale
      top-level Asset.value_after_depreciation field. Refused otherwise.

    Refuses to render if method isn't recognized, reason is empty (a
    disposal must state why — "end of useful life", "damaged beyond
    repair", "replaced by <asset>" — never just "dispose it"), or
    method == "sale" with no sale_proceeds stated.
    """
    if method not in METHODS:
        raise RenderError(f"method must be one of {METHODS}, got {method!r}")
    if not reason:
        raise RenderError(
            "reason is required — a disposal confirmation must state why, not just what."
        )
    if not isinstance(current_book_value, (int, float)) or isinstance(current_book_value, bool):
        raise RenderError(f"current_book_value must be numeric, got {current_book_value!r}")
    if book_value_source != "finance_books":
        raise RenderError(
            f"book_value_source must be 'finance_books', got {book_value_source!r} — "
            "current_book_value must be read from Asset.finance_books[N]."
            "value_after_depreciation, never the stale top-level "
            "Asset.value_after_depreciation field. Re-fetch from the correct field."
        )
    if method == "sale":
        if sale_proceeds is None or not isinstance(sale_proceeds, (int, float)) \
                or isinstance(sale_proceeds, bool):
            raise RenderError("sale_proceeds is required and must be numeric when method == 'sale'.")

    token_amount = sale_proceeds if method == "sale" else current_book_value
    token = disposal_token(asset, method, disposal_date, token_amount)

    lines = [
        f"# Asset disposal confirmation — `{asset}` ({asset_name})",
        "",
        "**Status: NOT DISPOSED YET. This is a confirmation request, not an action already "
        "taken. Double confirm required per this skill's non-negotiable: show this, then "
        "ask again before calling the disposal method.**",
        "",
        f"**Method:** {method}  |  **Disposal date:** {disposal_date}",
        f"**Reason:** {reason}",
        f"**Current book value:** {_fmt(current_book_value)}",
        "",
    ]

    if method == "scrap":
        lines += [
            "**What happens:** the full remaining book value "
            f"({_fmt(current_book_value)}) is written off via an auto-created Journal "
            "Entry. No consideration is received. `Asset.status` becomes `Scrapped`. "
            "This is a single call with no separate draft/review step of its own — "
            "this confirmation IS the review checkpoint.",
        ]
    else:
        gain_loss = round(sale_proceeds - current_book_value, 2)
        verdict = "GAIN" if gain_loss > 0 else "LOSS" if gain_loss < 0 else "no gain or loss"
        lines += [
            f"**Sale proceeds:** {_fmt(sale_proceeds)}",
            f"**Estimated {verdict}:** {_fmt(abs(gain_loss)) if gain_loss else '0.00'}",
            "",
            "**What happens:** a draft Sales Invoice is created for this asset. The "
            "gain/loss above is an estimate based on the current book value and stated "
            "proceeds — it is realized only when that Sales Invoice is submitted, which "
            "this skill does NOT do automatically. Route the drafted invoice to whoever "
            "owns Sales Invoice submission for this org.",
        ]

    lines += [
        "",
        "**Reminder:** cancel is the practical undo for a disposed, ledger-touching "
        "asset — deleting afterward will fail once the Journal Entry/Sales Invoice link "
        "exists (same `LinkExistsError` mechanism as any other submitted, ledger-touching "
        "ERPNext document).",
        "",
        f"**Confirmation token:** `{token}` — pass this exact token to "
        "`erp_client.call_whitelisted_method()` when calling `scrap_asset` / "
        "`make_sales_invoice`. The call is refused without it, and it only matches if "
        "it was computed from these same facts.",
    ]

    if notes:
        lines += ["", "## Notes", "", notes]

    lines += [
        "",
        "---",
        "*Confirm current_book_value was read from `finance_books[].value_after_depreciation` "
        "(not the stale top-level `Asset.value_after_depreciation` field — see "
        "render_depreciation_run.py) before relying on the gain/loss estimate above.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_disposal.py <path-to-json-input>", file=sys.stderr)
        print(
            'JSON shape: {"asset": "...", "asset_name": "...", "method": "scrap|sale", '
            '"disposal_date": "...", "current_book_value": 0, "sale_proceeds": 0, '
            '"reason": "...", "notes": "..."}',
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
        out = render_disposal(
            asset=payload["asset"],
            asset_name=payload["asset_name"],
            method=payload["method"],
            disposal_date=payload["disposal_date"],
            current_book_value=payload["current_book_value"],
            sale_proceeds=payload.get("sale_proceeds"),
            reason=payload.get("reason", ""),
            book_value_source=payload.get("book_value_source", "finance_books"),
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
