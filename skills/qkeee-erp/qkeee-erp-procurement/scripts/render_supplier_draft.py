#!/usr/bin/env python3
"""
qkeee-erp-procurement — Supplier onboarding draft renderer.

Enforces this skill's non-negotiable in code, not just in the prompt:
never present a Supplier as ready-to-create if a mandatory/KYC field is
missing or below the confidence threshold. A draft with any incomplete
field renders as INCOMPLETE — listing exactly what's missing — and the
CLI exits non-zero, so a calling skill can't accidentally treat an
incomplete draft as create-ready.

Field bar (confirmed live against <erp-instance>):
  - ERPNext's own hard-mandatory fields on Supplier are only
    `supplier_name` and `supplier_type` (see references/connector-
    reference.md) — everything else this script treats as "mandatory"
    (supplier_group, country, tax_id, default_bank_account) is this
    skill's own KYC bar, stricter than ERPNext's, per the
    "never onboard with incomplete KYC" non-negotiable. ERPNext will
    happily accept a Supplier missing all of them; this script won't
    stage one as ready.
  - `default_bank_account` is the real Supplier field (Link ->
    "Bank Account", confirmed live via `GET /api/resource/DocType/
    Supplier` — see references/erpnext-buying-docs.md). An earlier
    version of this script required three invented field names
    (`bank_account_name`/`bank_account_iban_or_number`/`bank_name`)
    that don't exist on the Supplier doctype at all — that mismatch
    is fixed here. The Bank Account record itself (account number,
    IBAN, bank name) is a separate doctype this script does not
    create or validate; if the target Bank Account record doesn't
    exist yet, the caller must create/resolve it first and pass its
    name as `default_bank_account`.
"""

import json
import sys

# This skill's own KYC bar — stricter than ERPNext's bare minimum
# (supplier_name + supplier_type only). Bank details are deliberately
# required here since a Supplier without a payable account on file
# can't actually be paid once a PO/Invoice exists against it.
REQUIRED_FIELDS = ("supplier_name", "supplier_group", "supplier_type", "country")
REQUIRED_KYC_FIELDS = ("tax_id",)
REQUIRED_BANK_FIELDS = ("default_bank_account",)
# Chosen as a conservative default (require human confirmation below
# 3-in-4 confidence) — not derived from measured extraction accuracy on
# any real document set. Treat as a tunable, not a validated figure;
# revisit once real doc-extraction confidence data exists for this skill.
MIN_CONFIDENCE = 0.75


class RenderError(Exception):
    pass


def _missing_or_low_confidence(fields: dict, confidence: dict, required: tuple) -> list:
    problems = []
    for f in required:
        val = fields.get(f)
        if val in (None, "", []):
            problems.append(f"{f}: missing")
            continue
        conf = confidence.get(f)
        if conf is not None and conf < MIN_CONFIDENCE:
            problems.append(f"{f}: low confidence ({conf:.2f}) — value '{val}' needs human confirmation")
    return problems


def render_supplier_draft(fields: dict, confidence: dict = None, source: str = "user-provided") -> dict:
    """
    fields: candidate Supplier field values (may come from
      qkeee-erp-doc-extraction or direct user input).
    confidence: optional {fieldname: 0.0-1.0} per-field confidence, from
      extraction. User-typed fields have no confidence entry and are
      treated as fully trusted.
    source: "user-provided" | "doc-extraction" | "mixed" — surfaced in
      the rendered report so a reviewer knows how much scrutiny each
      field needs.

    Returns {"status": "ready"|"incomplete", "markdown": str, "problems": [...]}.
    "ready" is necessary but not sufficient for create — the calling
    skill still needs explicit user confirmation (Stage/Confirm split).
    """
    confidence = confidence or {}
    problems = []
    problems += _missing_or_low_confidence(fields, confidence, REQUIRED_FIELDS)
    problems += _missing_or_low_confidence(fields, confidence, REQUIRED_KYC_FIELDS)

    bank_present = any(fields.get(f) for f in REQUIRED_BANK_FIELDS)
    if bank_present:
        # A low-confidence extraction of the linked Bank Account name is
        # exactly as much a gap as no bank account at all.
        problems += _missing_or_low_confidence(fields, confidence, REQUIRED_BANK_FIELDS)
    else:
        problems.append(
            "default_bank_account: missing — a Supplier with no linked Bank "
            "Account can't actually be paid once a PO/Invoice exists; confirm "
            "with the user whether bank details are being deferred deliberately "
            "(this script does not create the Bank Account record itself — "
            "resolve or create it first, then pass its name here)"
        )

    status = "incomplete" if problems else "ready"

    lines = [f"# Supplier onboarding draft — {status.upper()}", "", f"**Source:** {source}", ""]
    lines.append("## Fields")
    lines.append("")
    lines.append("| Field | Value | Confidence |")
    lines.append("| --- | --- | --- |")
    all_fields = REQUIRED_FIELDS + REQUIRED_KYC_FIELDS + REQUIRED_BANK_FIELDS
    seen = set()
    for f in all_fields + tuple(k for k in fields if k not in all_fields):
        if f in seen:
            continue
        seen.add(f)
        val = fields.get(f, "")
        conf = confidence.get(f)
        conf_str = f"{conf:.2f}" if conf is not None else ("n/a (user-typed)" if val else "—")
        lines.append(f"| {f} | {val or '*(empty)*'} | {conf_str} |")
    lines.append("")

    if problems:
        lines.append(f"## INCOMPLETE — {len(problems)} issue(s), not create-ready")
        lines.append("")
        for p in problems:
            lines.append(f"- {p}")
        lines.append("")
        lines.append(
            "This draft will not be created in ERPNext until every issue above "
            "is resolved and the user explicitly confirms."
        )
    else:
        lines.append(
            "## Ready to create — still requires explicit user confirmation "
            "before any write call (Confirm stage, not skipped by 'ready')."
        )
    lines.append("")

    return {"status": status, "markdown": "\n".join(lines), "problems": problems}


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_supplier_draft.py <path-to-json-input>", file=sys.stderr)
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

    result = render_supplier_draft(
        fields=payload.get("fields", {}),
        confidence=payload.get("confidence", {}),
        source=payload.get("source", "user-provided"),
    )

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(result["markdown"])

    sys.exit(0 if result["status"] == "ready" else 1)


if __name__ == "__main__":
    _cli()
