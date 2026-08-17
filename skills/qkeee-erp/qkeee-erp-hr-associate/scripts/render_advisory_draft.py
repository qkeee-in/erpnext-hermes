#!/usr/bin/env python3
"""
qkeee-erp-hr-associate — advisory-only draft renderer for Job Offer
(Offer Letter) and Employee Onboarding.

Structural enforcement of this skill's non-negotiable (module plan
Phase 6 decision): these two capabilities NEVER auto-commit, regardless
of `qkeee_erp.mode`. Unlike render_employee_draft.py's "ready" status
(which still requires a separate human Confirm step per the six-stage
pattern, but is at least a state a caller could — bug permitting —
chain into an immediate create call), this renderer has NO "ready"
status at all and no parameter that could ever request one. Every
output is `recommended_action: "advisory-only"` and the CLI always
exits non-zero — there is structurally no way for a caller to read a
"go ahead and create this" signal out of this script, mode or no mode.
The only path to an actual create call is the calling skill's own
SKILL.md-level Confirm step, invoking `erp_client.py mutate` directly
after that explicit human turn — never gated through this renderer.

Compensation sensitivity (Job Offer) and irreversible-in-practice
organizational commitment (both) are why these two capabilities carry a
stricter bar than the rest of this skill's read-write-capable
capabilities.
"""

import json
import sys

ALLOWED_DOCTYPES = ("Job Offer", "Employee Onboarding")


class RenderError(Exception):
    pass


def _fmt(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    # Numeric-looking strings (e.g. a compensation figure from a
    # doc-extraction pipeline, which hands back strings, not floats) still
    # get thousands-separator formatting — Job Offer is the one doctype
    # this renderer serves where an unformatted compensation figure is a
    # readability regression the human reviewer most needs to avoid.
    if isinstance(value, str):
        try:
            return f"{float(value):,.2f}" if "." in value else f"{int(value):,}"
        except ValueError:
            pass
    return str(value)


def render_advisory_draft(doctype: str, fields: dict, reason: str) -> dict:
    """
    doctype: "Job Offer" or "Employee Onboarding" only — any other value
      raises RenderError, since this script's whole purpose is a bar
      that's specific to these two capabilities.
    fields: candidate field values for the doctype (whatever the
      calling skill has gathered — this renderer doesn't validate
      completeness, only frames the output as advisory-only).
    reason: why this draft is advisory-only right now (e.g.
      "compensation terms pending Finance sign-off" / "background check
      not yet returned") — surfaced to the user so the gate reads as
      deliberate, not as a missing feature.

    Returns {"recommended_action": "advisory-only", "markdown": str}.
    No other recommended_action value exists in this module.
    """
    if doctype not in ALLOWED_DOCTYPES:
        raise RenderError(
            f"render_advisory_draft.py is only for {ALLOWED_DOCTYPES}, not '{doctype}'. "
            f"Use render_employee_draft.py or the report renderer for other capabilities."
        )

    lines = [f"# {doctype} draft — ADVISORY ONLY, not create-ready", ""]
    lines.append(
        f"**This capability never auto-commits, in any `qkeee_erp.mode`** "
        f"— {doctype} always requires the acting user to create/submit it "
        f"themselves in ERPNext directly (or explicitly instruct this skill "
        f"to do so as its own separate, deliberate step), never as a "
        f"continuation of this draft being marked ready."
    )
    lines.append("")
    lines.append(f"**Why advisory-only right now:** {reason}")
    lines.append("")
    lines.append("## Proposed fields")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    for k, v in fields.items():
        lines.append(f"| {k} | {_fmt(v) if v not in (None, '', []) else '*(empty)*'} |")
    lines.append("")

    return {"recommended_action": "advisory-only", "markdown": "\n".join(lines)}


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_advisory_draft.py <path-to-json-input>", file=sys.stderr)
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
        result = render_advisory_draft(
            doctype=payload["doctype"],
            fields=payload.get("fields", {}),
            reason=payload.get("reason", "compensation/organizational-commitment sensitivity"),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(result["markdown"])

    # Always non-zero — there is no "ready" exit code in this script.
    sys.exit(1)


if __name__ == "__main__":
    _cli()
