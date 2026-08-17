#!/usr/bin/env python3
"""
qkeee-erp-fixed-asset-manager — Asset Movement (transfer/issue/receipt)
draft renderer.

This persona treats location integrity as sacrosanct — a transfer drawn
up against the wrong "current location" silently corrupts the audit
trail (the receiving location looks right, but the movement history no
longer reflects where the asset actually was). Confirmed live against
<erp-instance>: Asset Movement's `source_location` is NOT cross-checked
against the asset's actual current `location` field by ERPNext itself
at create/submit time — a caller could submit a Transfer with a
fabricated or stale source_location and ERPNext would accept it. This
renderer closes that gap: it requires the caller to have just fetched
the asset's real current location and refuses to render if the declared
source_location doesn't match it (for Transfer/Issue purposes — Receipt
has no meaningful prior location to check).

This script NEVER calls ERPNext. It only formats a draft for human
review. Actually creating/submitting the Asset Movement is a separate,
later step gated on explicit confirmation of this rendered draft.
"""

import json
import sys
from datetime import datetime, timezone

PURPOSES = ("Issue", "Receipt", "Transfer", "Transfer and Issue")
REQUIRED_ITEM_KEYS = ("asset",)
MAX_LOCATION_STALENESS_SECONDS = 300


class RenderError(Exception):
    pass


def _fmt(value) -> str:
    return str(value) if value not in (None, "") else "-"


def render_movement_draft(purpose: str, company: str, transaction_date: str,
                           items: list, actual_current_locations: dict,
                           actual_current_locations_fetched_at: str = None,
                           now: str = None, notes: str = "") -> str:
    """
    items: list of {"asset": str, "source_location": str (optional),
      "target_location": str (optional), "from_employee": str (optional),
      "to_employee": str (optional)}.
    actual_current_locations: {asset_name: current_location_str} — the
      caller's live-fetched Asset.location values for every asset in
      `items`, fetched fresh just before calling this renderer (not
      cached/assumed). Required for every item whose purpose implies a
      "from" (Transfer, Issue, Transfer and Issue); Receipt items are
      exempt since Receipt has no meaningful prior location.
    actual_current_locations_fetched_at: ISO-8601 timestamp of when
      actual_current_locations was fetched. If provided, this renderer
      checks it's not older than MAX_LOCATION_STALENESS_SECONDS relative
      to `now` (defaults to current UTC time) and refuses to mark the
      draft ready if it is — closing the gap where a long session reuses
      a location snapshot from many turns earlier. If omitted, no
      freshness check is performed (backward-compatible, but the caller
      should supply it for any multi-turn or long-running session).

    Refuses to render if purpose is unrecognized, items is empty, any
    item is missing 'asset', (for Transfer/Issue/Transfer and Issue) a
    declared source_location doesn't match the actual current location,
    or actual_current_locations is stale beyond the staleness threshold
    — those mismatches are exactly the corruption this renderer exists
    to catch before it reaches ERPNext.
    """
    if purpose not in PURPOSES:
        raise RenderError(f"purpose must be one of {PURPOSES}, got {purpose!r}")
    if not items:
        raise RenderError("items is empty — an Asset Movement needs at least one asset line.")

    mismatches = []

    if actual_current_locations_fetched_at:
        fetched_dt = datetime.fromisoformat(actual_current_locations_fetched_at)
        now_dt = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
        if fetched_dt.tzinfo is None:
            fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        age_seconds = (now_dt - fetched_dt).total_seconds()
        if age_seconds > MAX_LOCATION_STALENESS_SECONDS:
            mismatches.append(
                f"actual_current_locations was fetched {age_seconds:.0f}s ago, older than "
                f"the {MAX_LOCATION_STALENESS_SECONDS}s staleness limit — refetch asset "
                "locations immediately before staging this as ready."
            )
    for it in items:
        missing = [k for k in REQUIRED_ITEM_KEYS if not it.get(k)]
        if missing:
            raise RenderError(f"Item missing required key(s) {missing}: {it}")
        needs_source = purpose in ("Issue", "Transfer", "Transfer and Issue")
        if needs_source:
            actual = actual_current_locations.get(it["asset"])
            declared = it.get("source_location")
            if actual is None:
                mismatches.append(
                    f"{it['asset']}: no actual_current_locations entry provided — cannot "
                    f"verify the declared source_location ({_fmt(declared)}) is correct."
                )
            elif declared != actual:
                mismatches.append(
                    f"{it['asset']}: declared source_location {_fmt(declared)} does not "
                    f"match the asset's actual current location {_fmt(actual)} — refetch "
                    f"and correct before staging this as ready."
                )

    is_ready = not mismatches

    lines = [
        f"# Asset Movement ({purpose}) — DRAFT, NOT CREATED",
        "",
        f"**Company:** {company}  |  **Transaction date:** {transaction_date}",
        "",
        "| Asset | Source location | Target location | From employee | To employee |",
        "| --- | --- | --- | --- | --- |",
    ]
    for it in items:
        lines.append(
            f"| {it['asset']} | {_fmt(it.get('source_location'))} | "
            f"{_fmt(it.get('target_location'))} | {_fmt(it.get('from_employee'))} | "
            f"{_fmt(it.get('to_employee'))} |"
        )
    lines.append("")

    if is_ready:
        lines.append(
            "**Status: READY. Staged for review — not yet created in ERPNext. Every "
            "declared source_location matches the asset's actual current location. "
            "Explicit confirmation required before Execute.**"
        )
    else:
        lines.append(f"**Status: INCOMPLETE — {len(mismatches)} location mismatch(es) below.**")
        lines.append("")
        lines.append("## Location mismatches")
        for m in mismatches:
            lines.append(f"- {m}")
    lines.append("")

    if notes:
        lines += ["## Notes", "", notes, ""]

    lines += [
        "---",
        "*ERPNext itself does not cross-check source_location against the asset's real "
        "current location at create/submit time — this renderer is the only place that "
        "check happens. Refetch actual_current_locations if any asset may have moved "
        "since it was last read.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_movement_draft.py <path-to-json-input>", file=sys.stderr)
        print(
            'JSON shape: {"purpose": "...", "company": "...", "transaction_date": "...", '
            '"items": [{"asset": "...", "source_location": "...", "target_location": "..."}], '
            '"actual_current_locations": {"ASSET-NAME": "location"}, "notes": "..."}',
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
        out = render_movement_draft(
            purpose=payload["purpose"],
            company=payload["company"],
            transaction_date=payload["transaction_date"],
            items=payload["items"],
            actual_current_locations=payload.get("actual_current_locations", {}),
            actual_current_locations_fetched_at=payload.get("actual_current_locations_fetched_at"),
            now=payload.get("now"),
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
