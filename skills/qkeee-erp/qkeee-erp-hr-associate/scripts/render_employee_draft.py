#!/usr/bin/env python3
"""
qkeee-erp-hr-associate — Employee draft renderer (new onboarding, or an
update to an existing record).

Enforces two things in code, not just in the prompt:
  1. ERPNext's own mandatory fields (confirmed live against
     <erp-instance>, 2026-08-10) must be present/confident before a draft
     is marked "ready" — first_name, gender, date_of_birth,
     date_of_joining, company.
  2. Any PII-sensitive field present in the draft (compensation, ID/
     passport numbers, personal contact details) is flagged explicitly
     as "contains sensitive PII — confirm this task is authorized to
     see/write it" rather than rendered silently alongside ordinary
     fields — this skill's non-negotiable is that PII never surfaces
     outside the scope of the current authorized task, and a renderer
     that treats a salary figure the same as a department name would
     make that easy to violate by accident.

Does NOT decide submission authority the way qkeee-erp-procurement's PO
renderer does — Employee is not a submittable doctype (confirmed live:
docstatus stays 0 permanently). The advisory-first override for
Employee Onboarding specifically (never auto-create even in read-write)
lives in render_advisory_draft.py, not here — this script covers a
plain Employee create/update, which Employee Onboarding's own
domain-knowledge treats as a downstream step of a bigger advisory-gated
process, not a capability of its own.
"""

import json
import sys

REQUIRED_FIELDS = ("first_name", "gender", "date_of_birth", "date_of_joining", "company")

# Confirmed live: Employee status "Left" requires a relieving_date —
# not enforced by the schema's reqd flag on relieving_date itself, the
# same "reqd flag isn't the whole story" pattern qkeee-erp-procurement's
# build found on Purchase Order's warehouse requirement.
LEFT_STATUS_REQUIRES = ("relieving_date",)

# Confirmed against <erp-instance>'s live Employee schema (2026-08-10
# adversarial review): `ctc` is the actual compensation figure, not just
# `salary_mode` metadata about it — the field this non-negotiable most
# needs to catch. `current_address`/`permanent_address`/`cell_number`
# cover "personal contact details" per SKILL.md's non-negotiable, same
# tier as `person_to_be_contacted`/`emergency_phone_number`, which were
# already flagged. `date_of_death` was removed — it doesn't exist on this
# instance's Employee schema (checked live), so it could never fire.
PII_SENSITIVE_FIELDS = (
    "ctc", "salary_currency", "salary_mode", "bank_ac_no", "bank_name",
    "iban", "passport_number", "health_details", "personal_email",
    "current_address", "permanent_address", "cell_number",
    "family_background", "emergency_phone_number", "person_to_be_contacted",
)


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
        if conf is not None and conf < 0.75:
            problems.append(f"{f}: low confidence ({conf:.2f}) — value '{val}' needs human confirmation")
    return problems


def render_employee_draft(fields: dict, confidence: dict = None, source: str = "user-provided",
                           is_update: bool = False) -> dict:
    """
    fields: candidate Employee field values.
    confidence: optional {fieldname: 0.0-1.0} per-field confidence, from
      extraction (e.g. an offer letter or ID document via doc-extraction).
    is_update: True for an update to an existing Employee — required
      fields are still checked (an update payload shouldn't blank a
      mandatory field), but the draft is framed as a change, not a new
      hire.

    Returns {"status": "ready"|"incomplete", "markdown": str,
             "problems": [...], "pii_fields_present": [...]}.
    """
    confidence = confidence or {}
    problems = _missing_or_low_confidence(fields, confidence, REQUIRED_FIELDS)

    if fields.get("status") == "Left":
        problems += _missing_or_low_confidence(fields, confidence, LEFT_STATUS_REQUIRES)

    pii_present = [f for f in PII_SENSITIVE_FIELDS if fields.get(f) not in (None, "", [])]

    status = "incomplete" if problems else "ready"

    kind = "Employee update" if is_update else "New employee onboarding"
    lines = [f"# {kind} draft — {status.upper()}", "", f"**Source:** {source}", ""]

    if pii_present:
        lines.append(
            f"**CONTAINS SENSITIVE PII ({len(pii_present)} field(s)) — confirm this "
            f"task is authorized to see/write these before proceeding:** "
            f"{', '.join(pii_present)}"
        )
        lines.append("")

    lines.append("## Fields")
    lines.append("")
    lines.append("| Field | Value | Confidence | PII |")
    lines.append("| --- | --- | --- | --- |")
    all_fields = REQUIRED_FIELDS + tuple(k for k in fields if k not in REQUIRED_FIELDS)
    seen = set()
    for f in all_fields:
        if f in seen:
            continue
        seen.add(f)
        val = fields.get(f, "")
        conf = confidence.get(f)
        conf_str = f"{conf:.2f}" if conf is not None else ("n/a (user-typed)" if val else "—")
        pii_mark = "yes" if f in PII_SENSITIVE_FIELDS else ""
        lines.append(f"| {f} | {val or '*(empty)*'} | {conf_str} | {pii_mark} |")
    lines.append("")

    if problems:
        lines.append(f"## INCOMPLETE — {len(problems)} issue(s), not create/update-ready")
        lines.append("")
        for p in problems:
            lines.append(f"- {p}")
        lines.append("")
    else:
        lines.append(
            "## Ready — still requires explicit user confirmation before any "
            "write call (Confirm stage, not skipped by 'ready')."
        )
    lines.append("")

    return {
        "status": status,
        "markdown": "\n".join(lines),
        "problems": problems,
        "pii_fields_present": pii_present,
    }


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_employee_draft.py <path-to-json-input>", file=sys.stderr)
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

    result = render_employee_draft(
        fields=payload.get("fields", {}),
        confidence=payload.get("confidence", {}),
        source=payload.get("source", "user-provided"),
        is_update=payload.get("is_update", False),
    )

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(result["markdown"])

    sys.exit(0 if result["status"] == "ready" else 1)


if __name__ == "__main__":
    _cli()
