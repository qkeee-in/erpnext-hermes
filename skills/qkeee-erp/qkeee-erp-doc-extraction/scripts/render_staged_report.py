#!/usr/bin/env python3
"""
qkeee-erp-doc-extraction — staged extraction report renderer.

Pure formatting utility: takes structured extraction output and renders it
as a Markdown report for human review. Never touches ERPNext, never
writes anything — this skill's non-negotiable is structural (it has no
ERPNext connector at all, not even a copy of erp_client.py), and this
script is part of why: it only ever produces a review artifact, not a
payload aimed at an API.

Confidence levels are the extraction step's judgment call, not this
script's — this file only renders whatever it's given, and refuses to
silently invent a confidence value for a field that's missing one.

Value semantics (both REQUIRED keys, not optional):
  - value is None   -> field not found in the source at all.
  - value is ""      -> field was found and is genuinely blank
                        (e.g. an optional middle_name left empty on a
                        form). Distinct from "not found" — don't conflate
                        the two, a blank-but-present field shouldn't read
                        as a missing one.
"""

import json
import sys

VALID_CONFIDENCE = {"high", "medium", "low"}
REQUIRED_KEYS = ("field", "value", "confidence")


class RenderError(Exception):
    pass


def _display_value(value):
    """Single source of truth for how a value renders — used both in the
    top-of-report attention list and the table body, so the two can't
    silently disagree (as they previously did: one used an explicit
    None/"" check, the other used a truthy `or` that misrendered 0/False
    as "not found")."""
    if value is None:
        return "*(not found)*"
    if value == "":
        return "*(blank)*"
    return str(value)


def render_staged_report(target_doctype: str, source_file: str, fields: list, notes: str = "") -> str:
    """
    fields: list of dicts, each REQUIRED to have:
      - field: str (ERPNext-doctype-shaped field name)
      - value: the extracted value, or None if not found, or "" if found
                and genuinely blank (see module docstring — these are
                NOT the same thing).
      - confidence: one of "high" | "medium" | "low"
      - source: short pointer to where in the doc this came from
                (e.g. "page 2, table row 3", "letterhead"), or None if
                the field wasn't found at all. Optional.
      - row: optional str identifying a repeating child-table row this
                field belongs to (e.g. "items[0]") — fields sharing the
                same `row` are rendered together as one line item, instead
                of colliding into a single flat table alongside header
                fields. Fields without `row` are treated as doctype
                header fields.

    Every field must carry an explicit confidence and an explicit value
    key (even if the value is None) — this function raises rather than
    defaulting either, since silently inventing one is exactly the
    failure mode this report exists to prevent.
    """
    for f in fields:
        missing = [k for k in REQUIRED_KEYS if k not in f]
        if missing:
            raise RenderError(f"Field entry missing required key(s) {missing}: {f}")
        if f["confidence"] not in VALID_CONFIDENCE:
            raise RenderError(
                f"Field '{f['field']}' has invalid confidence '{f['confidence']}' "
                f"(must be one of {sorted(VALID_CONFIDENCE)})."
            )

    def label(f):
        return f"{f['row']}.{f['field']}" if f.get("row") else f["field"]

    # "Not found" (value is None) is a distinct signal from "low
    # confidence" — a field can be present-but-shaky (medium/low
    # confidence, real value) or absent entirely (value None, any
    # confidence). Either is worth surfacing; a blank-but-present field
    # confirmed at high confidence is not.
    needs_attention = [f for f in fields if f["confidence"] == "low" or f["value"] is None]

    lines = [
        f"# Staged extraction - {target_doctype}",
        "",
        f"**Source:** {source_file}",
        "**Status:** NOT WRITTEN - review and confirm before any ERPNext create/update.",
        "",
    ]
    if needs_attention:
        lines.append(
            f"**{len(needs_attention)} field(s) need attention** (low confidence or not found) - "
            f"verify these before confirming:"
        )
        for f in needs_attention:
            lines.append(f"- `{label(f)}`: {_display_value(f['value'])}")
        lines.append("")

    header_fields = [f for f in fields if not f.get("row")]
    row_fields = [f for f in fields if f.get("row")]

    if header_fields:
        lines.append("| Field | Value | Confidence | Source |")
        lines.append("| --- | --- | --- | --- |")
        for f in header_fields:
            source_str = f.get("source") or "-"
            lines.append(f"| `{f['field']}` | {_display_value(f['value'])} | {f['confidence']} | {source_str} |")
        lines.append("")

    if row_fields:
        seen_rows = []
        for f in row_fields:
            if f["row"] not in seen_rows:
                seen_rows.append(f["row"])
        for row_id in seen_rows:
            lines.append(f"#### Line item: `{row_id}`")
            lines.append("")
            lines.append("| Field | Value | Confidence | Source |")
            lines.append("| --- | --- | --- | --- |")
            for f in row_fields:
                if f["row"] != row_id:
                    continue
                source_str = f.get("source") or "-"
                lines.append(f"| `{f['field']}` | {_display_value(f['value'])} | {f['confidence']} | {source_str} |")
            lines.append("")

    if notes:
        lines += ["## Notes", "", notes, ""]

    lines += [
        "---",
        "*This is a staged report, not an ERPNext record. Nothing has been written. "
        "Confirm field values (especially any flagged above) before handing off to "
        "the consuming skill's create/update capability.*",
    ]
    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_staged_report.py <path-to-json-input>", file=sys.stderr)
        print(
            'JSON shape: {"target_doctype": "...", "source_file": "...", '
            '"fields": [{"field": "...", "value": ..., "confidence": "high|medium|low", '
            '"source": "...", "row": "..."}], "notes": "..."}',
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
        report = render_staged_report(
            target_doctype=payload["target_doctype"],
            source_file=payload["source_file"],
            fields=payload["fields"],
            notes=payload.get("notes", ""),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Extracted values (foreign supplier names, accented characters,
    # currency symbols) can contain arbitrary Unicode; a legacy-codepage
    # Windows console will raise UnicodeEncodeError on a plain print().
    # Reconfigure stdout to replace unencodable characters instead of
    # crashing on real extracted data.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(report)


if __name__ == "__main__":
    _cli()
