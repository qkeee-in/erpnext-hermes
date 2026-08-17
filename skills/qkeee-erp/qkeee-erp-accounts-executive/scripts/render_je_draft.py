#!/usr/bin/env python3
"""
qkeee-erp-accounts-executive — Journal Entry draft renderer.

Enforces the skill's non-negotiable in code, not just the prompt: a JE
draft cannot render unless its rows already balance (total debit ==
total credit) and every row has exactly one of debit/credit set. This
mirrors ERPNext's own submit-time validation ("Total Debit and Total
Credit must be equal before submission" — see
references/erpnext-accounting-docs.md) but catches it at DRAFT time,
before the row ever reaches ERPNext, so a reviewer never sees an
unbalanced entry presented as ready for their sign-off.

This script NEVER calls ERPNext. It only formats a draft for human
review. Actually creating/submitting the JE (via
scripts/erp_client.py's mutate_resource) is a separate, later step that
this skill's SKILL.md gates behind an explicit user confirm — advisory-
first, always, independent of qkeee_erp.mode. Nothing in this file
performs or enables that write.
"""

import json
import sys

REQUIRED_ROW_KEYS = ("account", "debit", "credit")


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


def render_je_draft(company: str, posting_date: str, narration: str, rows: list,
                     source_reference: str = "", notes: str = "") -> str:
    """
    rows: list of dicts, each REQUIRED to have:
      - account: str (must be a real, confirmed account name — never a
                  guessed one; this function has no way to verify that
                  itself, it can only enforce the arithmetic)
      - debit: numeric, 0 if this row is a credit
      - credit: numeric, 0 if this row is a debit
      Optional: cost_center, party, party_type, reference (voucher this
      row relates to, e.g. an invoice being written off).

    Refuses to render if:
      - rows is empty
      - any row is missing a required key, has a non-numeric debit/credit,
        or has BOTH debit and credit non-zero (or both zero) on the same
        row — a row must be unambiguously a debit or a credit, never both
        and never neither
      - total debit != total credit (within floating-point tolerance)

    This is the enforcement point for "debits must equal credits before
    the draft is even shown."
    """
    if len(rows) < 2:
        raise RenderError(
            f"Got {len(rows)} row(s). A Journal Entry draft needs at least two rows - "
            f"a single row can never balance (one side would have to be zero, which is "
            f"itself refused below as 'neither debit nor credit set')."
        )

    for r in rows:
        missing = [k for k in REQUIRED_ROW_KEYS if k not in r]
        if missing:
            raise RenderError(f"Row missing required key(s) {missing}: {r}")
        for key in ("debit", "credit"):
            if not isinstance(r[key], (int, float)) or isinstance(r[key], bool):
                raise RenderError(f"Row '{r['account']}' has non-numeric {key}: {r[key]!r}")
        debit_set = r["debit"] != 0
        credit_set = r["credit"] != 0
        if debit_set and credit_set:
            raise RenderError(
                f"Row '{r['account']}' has both a debit ({r['debit']}) and a credit "
                f"({r['credit']}) - a row must be unambiguously one or the other."
            )
        if not debit_set and not credit_set:
            raise RenderError(f"Row '{r['account']}' has neither a debit nor a credit set.")

    total_debit = sum(r["debit"] for r in rows)
    total_credit = sum(r["credit"] for r in rows)
    difference = round(total_debit - total_credit, 2)
    if abs(difference) > 0.005:
        raise RenderError(
            f"Draft does not balance: total debit {total_debit:,.2f} != total credit "
            f"{total_credit:,.2f} (difference {difference:,.2f}). Fix the rows before "
            f"drafting - this is the non-negotiable, enforced here, not something to "
            f"paper over with a rounding line unless that rounding line is itself one "
            f"of the declared rows."
        )

    lines = [
        "# Journal Entry — DRAFT, NOT SUBMITTED",
        "",
        f"**Company:** {company}  |  **Posting date:** {posting_date}",
        f"**Narration:** {narration}",
    ]
    if source_reference:
        lines.append(f"**Reference:** {source_reference}")
    lines += [
        "",
        "**Status: staged for review. This has not been created or submitted in "
        "ERPNext. Advisory-first — this skill never auto-submits a Journal Entry, "
        "regardless of qkeee_erp.mode. Explicit confirmation is required before "
        "Execute.**",
        "",
        "| Account | Debit | Credit | Cost Center | Party | Reference |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['account']} | {_fmt(r['debit']) if r['debit'] else ''} | "
            f"{_fmt(r['credit']) if r['credit'] else ''} | {r.get('cost_center', '-')} | "
            f"{r.get('party', '-')} | {r.get('reference', '-')} |"
        )
    lines.append(f"| **Total** | **{_fmt(total_debit)}** | **{_fmt(total_credit)}** | | | |")
    lines.append("")
    lines.append(f"**Balance check:** debit {_fmt(total_debit)} == credit {_fmt(total_credit)} - ties out.")
    lines.append("")

    if notes:
        lines += ["## Notes", "", notes, ""]

    lines += [
        "---",
        "*Confirm every account name against the real chart of accounts before "
        "approving — this renderer enforces that the draft balances, not that the "
        "accounts named actually exist or are the right ones.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_je_draft.py <path-to-json-input>", file=sys.stderr)
        print(
            'JSON shape: {"company": "...", "posting_date": "...", "narration": "...", '
            '"rows": [{"account": "...", "debit": 0, "credit": 0, "cost_center": "...", '
            '"party": "...", "reference": "..."}], "source_reference": "...", "notes": "..."}',
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
        draft = render_je_draft(
            company=payload["company"],
            posting_date=payload["posting_date"],
            narration=payload["narration"],
            rows=payload["rows"],
            source_reference=payload.get("source_reference", ""),
            notes=payload.get("notes", ""),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(draft)


if __name__ == "__main__":
    _cli()
