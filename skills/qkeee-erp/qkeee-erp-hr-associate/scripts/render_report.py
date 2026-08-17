#!/usr/bin/env python3
"""
qkeee-erp-hr-associate — operational/HR report renderer (headcount,
birthdays/anniversaries, probation-ending visibility, leave balance,
attendance summary).

Same reconciliation-gate discipline as the other read-write persona
skills' render_report.py: refuses to render without either a declared,
well-formed reconciliation check or an explicit
`reconciliation_checks="not_applicable"` opt-out with a reason (e.g. a
birthday/anniversary list has nothing numeric to tie out). A failed
check is surfaced prominently, never hidden.
"""

import html
import json
import sys

REQUIRED_CHECK_KEYS = ("check", "expected", "actual", "ties_out")
NOT_APPLICABLE = "not_applicable"


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


def _diff(actual, expected) -> str:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) \
            and not isinstance(expected, bool) and not isinstance(actual, bool):
        return _fmt(actual - expected)
    return f"'{actual}' vs expected '{expected}'"


def render_report(title: str, scope: str, sections: list,
                   reconciliation_checks, notes: str = "") -> str:
    """
    scope: free-text context line (e.g. "As of 2026-08-10" or
      "Department: Engineering | Period: Aug 2026").
    sections: list of {"title": str, "rows": [{"label": str, "value": num
      or str}], "total": optional {"label": str, "value": num}}.
    reconciliation_checks: a non-empty list of well-formed check dicts
      (check/expected/actual/ties_out, numeric expected/actual), or the
      literal string "not_applicable" for a report with genuinely
      nothing to tie out (e.g. a birthday list, not a total) — must
      carry a reason in notes when used.
    """
    # Validate section rows first — an input malformed enough to raise here
    # shouldn't have its reconciliation_checks treated as already "clean"
    # by anything that might inspect the exception mid-validation.
    for s in sections:
        for r in s.get("rows", []):
            val = r["value"]
            if isinstance(val, (int, float)) and isinstance(val, bool):
                raise RenderError(f"Row '{r['label']}' in section '{s['title']}' has a boolean value: {val!r}")

    if reconciliation_checks != NOT_APPLICABLE:
        if not reconciliation_checks:
            raise RenderError(
                "No reconciliation_checks declared. Every report must self-check at "
                "least one tie-out, or explicitly pass reconciliation_checks="
                "'not_applicable' with a reason in notes."
            )
        for c in reconciliation_checks:
            missing = [k for k in REQUIRED_CHECK_KEYS if k not in c]
            if missing:
                raise RenderError(f"Reconciliation check missing required key(s) {missing}: {c}")
            for key in ("expected", "actual"):
                if not isinstance(c[key], (int, float)) or isinstance(c[key], bool):
                    raise RenderError(
                        f"Reconciliation check '{c['check']}' has non-numeric {key}: {c[key]!r}"
                    )
    elif not notes:
        raise RenderError(
            "reconciliation_checks='not_applicable' requires a reason in notes."
        )

    failures = [] if reconciliation_checks == NOT_APPLICABLE else [
        c for c in reconciliation_checks if not c["ties_out"]
    ]

    lines = [f"# {title}", "", f"**Scope:** {scope}", ""]

    if failures:
        lines.append(f"**ANOMALY — {len(failures)} reconciliation check(s) did not tie out:**")
        for c in failures:
            lines.append(
                f"- `{c['check']}`: expected {_fmt(c['expected'])}, got {_fmt(c['actual'])} "
                f"(diff {_diff(c['actual'], c['expected'])})"
            )
        lines.append("")

    for s in sections:
        lines.append(f"## {s['title']}")
        lines.append("")
        lines.append("| | Value |")
        lines.append("| --- | ---: |")
        for r in s.get("rows", []):
            val = r["value"]
            lines.append(f"| {r['label']} | {_fmt(val) if isinstance(val, (int, float)) else val} |")
        if s.get("total"):
            lines.append(f"| **{s['total']['label']}** | **{_fmt(s['total']['value'])}** |")
        lines.append("")

    lines.append("## Reconciliation")
    lines.append("")
    if reconciliation_checks == NOT_APPLICABLE:
        lines.append("*No tie-out check applies to this report* — declared `not_applicable`. See Notes for why.")
    else:
        lines.append("| Check | Expected | Actual | Ties out |")
        lines.append("| --- | ---: | ---: | :---: |")
        for c in reconciliation_checks:
            mark = "yes" if c["ties_out"] else "**NO**"
            lines.append(f"| {c['check']} | {_fmt(c['expected'])} | {_fmt(c['actual'])} | {mark} |")
    lines.append("")

    if notes:
        lines += ["## Notes", "", notes, ""]

    return "\n".join(lines)


def html_wrapper(title: str, markdown_body: str) -> str:
    escaped = html.escape(markdown_body)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        f"<style>body{{font-family:sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}}"
        f"pre{{white-space:pre-wrap}}</style></head><body><pre>{escaped}</pre></body></html>"
    )


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_report.py <path-to-json-input>", file=sys.stderr)
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
        report = render_report(
            title=payload["title"],
            scope=payload["scope"],
            sections=payload.get("sections", []),
            reconciliation_checks=payload["reconciliation_checks"],
            notes=payload.get("notes", ""),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if payload.get("format") == "html":
        report = html_wrapper(payload["title"], report)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(report)


if __name__ == "__main__":
    _cli()
