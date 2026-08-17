#!/usr/bin/env python3
"""
qkeee-erp-accounts-executive — cancel-confirmation renderer.

The non-negotiable says "never submit OR cancel a financial document
without explicit user confirmation" — but until this file existed, only
submit had a staged artifact (render_je_draft.py) a human actually
reviews before confirming. Cancel had none: an agent could call
mutate_resource(..., "cancel", ...) straight off a user's word with
nothing shown first. This closes that gap the same way render_je_draft.py
closes it for submit: refuses to render (and therefore blocks the
workflow from reaching Confirm) unless the record's identifying details
and financial impact are actually stated, not just a bare "cancel it"
request passed straight through.

This script NEVER calls ERPNext. The record being cancelled must already
have been fetched (e.g. via query_resource or the GET step submit uses)
so its real fields can be shown here — this file only formats what's
already known for human review before the actual mutate_resource(...,
"cancel", ...) call, which is a separate, later step gated on explicit
confirmation of THIS rendered output.
"""

import json
import sys

REQUIRED_KEYS = ("doctype", "name", "reason")


class RenderError(Exception):
    pass


def render_cancel_confirmation(doctype: str, name: str, reason: str,
                                impact: list = None, notes: str = "") -> str:
    """
    impact: list of {"label": str, "value": ...} — the financial/state
      facts a reviewer needs to see before confirming a cancel (e.g.
      total amount, linked GL entries, outstanding balance it will
      reopen). Not required to be numeric (unlike render_report.py's
      reconciliation rows) since some impact facts are descriptive
      ("this JE's Cash - QL debit of 100 will be reversed"), but the list
      itself must be non-empty — a cancel confirmation with zero stated
      impact is exactly the "confirm blind" failure mode this exists to
      prevent.

    Refuses to render if doctype/name/reason are missing, or if impact is
    empty — there is no such thing as a cancel with no impact to state;
    if genuinely nothing material changes, that itself is the impact
    statement ("no downstream financial effect — draft was never
    submitted") and must be said explicitly, not omitted.
    """
    provided = {"doctype": doctype, "name": name, "reason": reason}
    missing = [k for k in REQUIRED_KEYS if not provided.get(k)]
    if missing:
        raise RenderError(f"Missing required field(s) for cancel confirmation: {missing}")
    if not impact:
        raise RenderError(
            "No impact stated. A cancel confirmation must say what changes - even "
            "'no downstream financial effect' is a statement, not an omission."
        )

    lines = [
        f"# Cancel confirmation - {doctype} `{name}`",
        "",
        "**Status: NOT CANCELLED YET. This is a confirmation request, not an action "
        "already taken. Explicit confirmation of THIS summary is required before "
        "`mutate cancel` is called.**",
        "",
        f"**Reason for cancelling:** {reason}",
        "",
        "## What this cancel will change",
        "",
    ]
    for item in impact:
        lines.append(f"- **{item['label']}:** {item['value']}")
    lines.append("")
    lines.append(
        "**Reminder:** cancel is the practical undo for a submitted, ledger-touching "
        "document - `delete` will not work afterward (a cancelled voucher still has a "
        "linked GL Entry; ERPNext refuses the delete with `LinkExistsError`). Cancelling "
        "is the end state, not an intermediate step toward removing the record entirely."
    )

    if notes:
        lines += ["", "## Notes", "", notes]

    lines += [
        "",
        "---",
        "*Confirm this summary reflects what should actually happen before proceeding "
        "- this renderer enforces that an impact was stated, not that the stated impact "
        "is correct.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_cancel_confirmation.py <path-to-json-input>", file=sys.stderr)
        print(
            'JSON shape: {"doctype": "...", "name": "...", "reason": "...", '
            '"impact": [{"label": "...", "value": "..."}], "notes": "..."}',
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
        out = render_cancel_confirmation(
            doctype=payload["doctype"],
            name=payload["name"],
            reason=payload["reason"],
            impact=payload.get("impact", []),
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
