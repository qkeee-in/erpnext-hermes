#!/usr/bin/env python3
"""
qkeee-erp-fixed-asset-manager — depreciation-run confirmation renderer.

The non-negotiable: a depreciation run never executes without explicit
confirmation, and gets a DOUBLE confirm (state the financial impact in
plain terms, then ask again) given its irreversibility in practice.

Two live findings from building this skill against <erp-instance> drive
this renderer's shape:

  1. ERPNext's whitelisted depreciation-posting method,
     erpnext.assets.doctype.asset.depreciation.make_depreciation_entry
     (asset_depr_schedule_name), posts EVERY due-and-unposted row on the
     schedule in ONE call, not just the next single period — confirmed
     live: calling it once against a 6-months-overdue schedule posted
     six separate Journal Entries in that single call. A user asked to
     confirm "run depreciation" without being told this could believe
     they're approving one period when they're approving several.
  2. Asset's own top-level `value_after_depreciation` field does NOT
     update after a run — confirmed live, it stayed at the original
     gross amount after 6 periods posted. The real current book value
     lives on the child table, `finance_books[N].value_after_depreciation`
     / `total_number_of_booked_depreciations`. This renderer computes
     the resulting book value from the schedule rows directly, not from
     the (stale) top-level field, and documents this so a caller doesn't
     re-introduce the same mistake.

This script NEVER calls ERPNext. It only formats the confirmation. The
actual `make_depreciation_entry` call is a separate, later step gated on
explicit confirmation of THIS rendered output — and SKILL.md requires
asking again after showing it (double confirm), not treating one
"yes" as covering both the concept and the specifics.
"""

import json
import sys
import time

from confirm_token import depreciation_run_token

REQUIRED_ROW_KEYS = ("schedule_date", "depreciation_amount", "accumulated_depreciation_amount")


class RenderError(Exception):
    pass


def _fmt(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value)


def render_depreciation_run(asset: str, asset_depr_schedule: str, as_of_date: str,
                             opening_book_value: float, pending_rows: list,
                             book_value_source: str = "finance_books",
                             notes: str = "") -> str:
    """
    pending_rows: every depreciation_schedule row with schedule_date <=
      as_of_date AND an empty journal_entry — i.e. exactly what a single
      make_depreciation_entry call against this schedule would post.
      Each row: {"schedule_date": str, "depreciation_amount": num,
                 "accumulated_depreciation_amount": num}. Caller is
      responsible for having filtered to pending-only; this function
      does not re-derive "pending" from a fuller schedule dump.

    book_value_source: must be the literal "finance_books" — the caller
      must state, not just be trusted, that opening_book_value came from
      Asset.finance_books[N].value_after_depreciation and NOT the stale
      top-level Asset.value_after_depreciation field. Any other value
      (including the honest "top_level" admission) is refused: this is
      the one field this renderer can actually check, so it's checked.

    Refuses to render if pending_rows is empty (nothing to confirm — a
    caller should have already told the user "nothing due", not reached
    this renderer), if opening_book_value is non-numeric, or if
    book_value_source isn't the required literal.
    """
    if not pending_rows:
        raise RenderError(
            "pending_rows is empty — there is nothing due to post. Do not call this "
            "renderer for a no-op run; tell the user directly that nothing is due."
        )
    if not isinstance(opening_book_value, (int, float)) or isinstance(opening_book_value, bool):
        raise RenderError(f"opening_book_value must be numeric, got {opening_book_value!r}")
    if book_value_source != "finance_books":
        raise RenderError(
            f"book_value_source must be 'finance_books', got {book_value_source!r} — "
            "opening_book_value must be read from Asset.finance_books[N]."
            "value_after_depreciation, never the stale top-level "
            "Asset.value_after_depreciation field. Re-fetch from the correct field."
        )

    for r in pending_rows:
        missing = [k for k in REQUIRED_ROW_KEYS if k not in r]
        if missing:
            raise RenderError(f"Pending row missing required key(s) {missing}: {r}")
        for key in ("depreciation_amount", "accumulated_depreciation_amount"):
            if not isinstance(r[key], (int, float)) or isinstance(r[key], bool):
                raise RenderError(f"Row {r['schedule_date']} has non-numeric {key}: {r[key]!r}")

    total_depreciation = sum(r["depreciation_amount"] for r in pending_rows)
    resulting_book_value = round(opening_book_value - total_depreciation, 2)
    resulting_accumulated = pending_rows[-1]["accumulated_depreciation_amount"]
    issued_at = int(time.time())
    token = depreciation_run_token(asset, asset_depr_schedule, as_of_date, total_depreciation, issued_at)

    lines = [
        f"# Depreciation run confirmation — Asset `{asset}`",
        "",
        "**Status: NOT POSTED YET. This is a confirmation request, not an action already "
        "taken. Double confirm required per this skill's non-negotiable: show this, then "
        "ask again before calling `make_depreciation_entry`.**",
        "",
        f"**Schedule:** {asset_depr_schedule}  |  **As of:** {as_of_date}",
        "",
        f"**{len(pending_rows)} period(s) are due and will ALL post in one call** — "
        "ERPNext's depreciation-posting method posts every overdue period at once, not "
        "one at a time. If the user only expects one period's worth, say so explicitly "
        "before proceeding.",
        "",
        "| Schedule date | Depreciation amount | Accumulated after |",
        "| --- | ---: | ---: |",
    ]
    for r in pending_rows:
        lines.append(
            f"| {r['schedule_date']} | {_fmt(r['depreciation_amount'])} | "
            f"{_fmt(r['accumulated_depreciation_amount'])} |"
        )
    lines += [
        "",
        f"**Opening book value:** {_fmt(opening_book_value)}",
        f"**Total depreciation this run:** {_fmt(total_depreciation)}",
        f"**Resulting book value:** {_fmt(resulting_book_value)}",
        f"**Resulting accumulated depreciation:** {_fmt(resulting_accumulated)}",
        "",
        f"**{len(pending_rows)} separate Journal Entries will be created and submitted**, "
        "one per period — not one combined entry.",
        "",
        "**Note:** after this run, `Asset.value_after_depreciation` will NOT reflect the "
        "new book value (confirmed not to update on this ERPNext version) — read "
        "`finance_books[].value_after_depreciation` instead when reporting the result "
        "back to the user.",
        "",
        f"**Confirmation token:** `{token}` (issued_at: `{issued_at}`) — pass BOTH exact "
        "values to `erp_client.call_whitelisted_method()` (the token as `confirmation_token`, "
        "issued_at inside `token_facts`) when calling `make_depreciation_entry`. The call is "
        "refused without a matching token, and refused again if more than 15 minutes have "
        "passed since issued_at — re-render if that happens.",
    ]

    if notes:
        lines += ["", "## Notes", "", notes]

    lines += [
        "",
        "---",
        "*This renderer enforces that the pending-period impact is stated in full before "
        "either confirmation — it does not verify the pending_rows against a live schedule "
        "fetch itself; that fetch must have just happened.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_depreciation_run.py <path-to-json-input>", file=sys.stderr)
        print(
            'JSON shape: {"asset": "...", "asset_depr_schedule": "...", "as_of_date": "...", '
            '"opening_book_value": 0, "pending_rows": [{"schedule_date": "...", '
            '"depreciation_amount": 0, "accumulated_depreciation_amount": 0}], "notes": "..."}',
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
        out = render_depreciation_run(
            asset=payload["asset"],
            asset_depr_schedule=payload["asset_depr_schedule"],
            as_of_date=payload["as_of_date"],
            opening_book_value=payload["opening_book_value"],
            pending_rows=payload["pending_rows"],
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
