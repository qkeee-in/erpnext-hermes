#!/usr/bin/env python3
"""
qkeee-erp-mis-analyst — financial/management report renderer.

Enforces the skill's non-negotiable at the code layer, not just the
prompt: a report cannot render without either at least one declared
reconciliation check (e.g. trial balance debit total == credit total,
balance sheet assets == liabilities + equity, drill-down sum == parent
figure) or an explicit, reasoned opt-out for reports with nothing to tie
out (see NOT_APPLICABLE below). This mirrors qkeee-erp-doc-extraction's
render_staged_report.py enforcing confidence/value keys — the script is
the place the guarantee actually lives, not something the agent is
trusted to remember.

A mismatch is NOT refused — this persona "surfaces anomalies rather
than smoothing them over," so a tie-out failure renders prominently at
the top of the report instead of blocking output. What IS refused is a
report with no reconciliation stance declared at all, one missing a
required key, or a row value that isn't numeric (a wrongly-typed figure
is exactly the kind of bug this report exists to catch, not silently
stringify).

Output is Markdown always; wrap in html_wrapper() for the HTML variant
the module plan's UI/Visualization convention offers as a user choice.
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
    """Diff shown next to a failed check. Numeric pairs get a signed
    numeric diff; anything else (e.g. two differently-formatted strings
    that were expected to match) still shows both values rather than
    throwing the mismatch detail away as 'n/a'."""
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) \
            and not isinstance(expected, bool) and not isinstance(actual, bool):
        return _fmt(actual - expected)
    return f"'{actual}' vs expected '{expected}'"


def compute_variance(actual: float, comparison: float) -> dict:
    """Variance helper with the divide-by-zero guard domain-knowledge.md
    calls for, done once in code instead of left to be reimplemented (or
    forgotten) inline each time a variance report is built.

    Returns {"variance": actual - comparison, "variance_pct": float or
    None}. variance_pct is None when comparison == 0 — callers must
    render that as "n/a (base is zero)", never fabricate a percentage.
    """
    variance = actual - comparison
    variance_pct = (variance / comparison) if comparison != 0 else None
    return {"variance": variance, "variance_pct": variance_pct}


def render_report(title: str, period: str, company: str, sections: list,
                   reconciliation_checks, notes: str = "") -> str:
    """
    sections: list of {"title": str, "rows": [{"label": str, "value": num}],
                        "total": optional {"label": str, "value": num}}
    reconciliation_checks: EITHER
      - a non-empty list of dicts, each REQUIRED to have:
          check: str, what was compared (e.g. "trial balance: debit vs credit")
          expected: the reference figure
          actual: the figure actually computed/queried
          ties_out: bool
      - OR the literal string "not_applicable", for a report with no
        meaningful total to tie out (e.g. "list suppliers with zero
        purchases this year"). This is an explicit, visible opt-out, not
        a silent bypass — it renders its own line in the report so a
        reader can see the skill made this call rather than skipped the
        check by accident. Reach for a real check first; only use this
        when nothing plausible exists to compare.

    Refuses to render if reconciliation_checks is an empty list, isn't
    one of the two accepted shapes, or contains an entry missing a
    required key or a non-numeric expected/actual/row value — this is
    the enforcement point for the "numbers must tie out before
    presenting" non-negotiable.
    """
    if reconciliation_checks != NOT_APPLICABLE:
        if not reconciliation_checks:
            raise RenderError(
                "No reconciliation_checks declared. Every MIS report must self-check "
                "at least one tie-out (e.g. debit vs credit, drill-down sum vs parent "
                "figure) before it can render, or explicitly pass "
                "reconciliation_checks='not_applicable' with a reason in notes — "
                "this is the non-negotiable, enforced here."
            )
        for c in reconciliation_checks:
            missing = [k for k in REQUIRED_CHECK_KEYS if k not in c]
            if missing:
                raise RenderError(f"Reconciliation check missing required key(s) {missing}: {c}")
            for key in ("expected", "actual"):
                if not isinstance(c[key], (int, float)) or isinstance(c[key], bool):
                    raise RenderError(
                        f"Reconciliation check '{c['check']}' has non-numeric {key}: "
                        f"{c[key]!r} — figures must be numeric, not stringified."
                    )

    for s in sections:
        for r in s.get("rows", []):
            if not isinstance(r["value"], (int, float)) or isinstance(r["value"], bool):
                raise RenderError(
                    f"Row '{r['label']}' in section '{s['title']}' has non-numeric value "
                    f"{r['value']!r} — figures must be numeric, not stringified."
                )

    failures = [] if reconciliation_checks == NOT_APPLICABLE else [
        c for c in reconciliation_checks if not c["ties_out"]
    ]

    lines = [f"# {title}", "", f"**Period:** {period}  |  **Company:** {company}", ""]

    if failures:
        lines.append(f"**ANOMALY — {len(failures)} reconciliation check(s) did not tie out:**")
        for c in failures:
            lines.append(
                f"- `{c['check']}`: expected {_fmt(c['expected'])}, got {_fmt(c['actual'])} "
                f"(diff {_diff(c['actual'], c['expected'])})"
            )
        lines.append("")
        lines.append("*Figures below are presented as queried — discrepancies are surfaced, not smoothed over.*")
        lines.append("")

    for s in sections:
        lines.append(f"## {s['title']}")
        lines.append("")
        lines.append("| | Amount |")
        lines.append("| --- | ---: |")
        for r in s.get("rows", []):
            lines.append(f"| {r['label']} | {_fmt(r['value'])} |")
        if s.get("total"):
            lines.append(f"| **{s['total']['label']}** | **{_fmt(s['total']['value'])}** |")
        lines.append("")

    lines.append("## Reconciliation")
    lines.append("")
    if reconciliation_checks == NOT_APPLICABLE:
        lines.append(
            "*No tie-out check applies to this report* — declared `not_applicable` "
            "rather than omitted. See Notes for why."
        )
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
    """Minimal, dependency-free Markdown-to-HTML wrapper for the HTML report
    option. Not a general Markdown renderer — just enough structure
    (headings, tables already being pipe-tables) to make a report
    shareable/archivable, per the UI and Visualization convention.
    """
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
        print(
            'JSON shape: {"title": "...", "period": "...", "company": "...", '
            '"sections": [{"title": "...", "rows": [{"label": "...", "value": 0}], '
            '"total": {"label": "...", "value": 0}}], '
            '"reconciliation_checks": [{"check": "...", "expected": 0, "actual": 0, '
            '"ties_out": true}] OR "not_applicable", '
            '"notes": "...", "format": "markdown|html"}',
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
        report = render_report(
            title=payload["title"],
            period=payload["period"],
            company=payload["company"],
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
