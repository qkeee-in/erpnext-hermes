#!/usr/bin/env python3
"""
qkeee-erp-frappe-core -> persona-skill sync script.

Pushes core's canonical connector layer out to every persona skill's own
copy WITHOUT clobbering persona-specific additions. This is a merge, not
an overwrite:

- erp_client.py: replaces/adds only the named shared functions (see
  SHARED_FUNCTIONS below) by function name. Any other function in a
  persona's copy (persona-specific business logic, e.g.
  qkeee-erp-system-admin's destructive_mutate()) is left completely
  untouched, in its original position — as is every other top-level
  construct (module docstring, imports, constants, comments) that sits
  between functions; only named-function bodies are ever replaced.
  `_cli()` is deliberately NEVER touched — every persona copy has
  hand-extended CLI wiring for its own extra functions; overwriting it
  would delete that. Functions core has that a persona copy is missing
  are appended near the end of the file (before a trailing
  `if __name__ == "__main__":` guard, if present) — UNLESS the function
  is in WRITE_PATH_FUNCTIONS and the target skill is in
  READ_ONLY_SKILLS, in which case it's deliberately never (re)appended.
  This "append what's missing" behavior is exactly what silently broke
  qkeee-erp-mis-analyst once already (2026-08-18 postmortem): mis-analyst
  intentionally ships no write path at all, but a routine sync run had no
  way to know that "missing" here meant "deliberately omitted" rather
  than "never built yet," and reintroduced mutate_resource/_do_mutate/
  record_comment/_diff_fields/_audit_update/_audit_submit/
  record_audit_log_start/record_audit_log_finish as dead, unreachable
  code (no `mutate` CLI subcommand ever called them) — worse, the
  exception classes mutate_resource() raised on failure,
  ReadOnlyModeError/MissingRequesterError, were never even defined in
  that file, since SHARED_FUNCTIONS matching only looks at `def` blocks,
  not `class`. READ_ONLY_SKILLS/WRITE_PATH_FUNCTIONS below close that gap
  structurally instead of relying on nobody ever forgetting again.
- confirm_token.py: same function-level merge, applied only to the skills
  that already have this file (opt-in infra, not every persona needs
  double-confirm tokens).
- discover.py / test_discover.py: full copy to every persona skill (new
  capability, no existing persona content to preserve), with the
  persona-code placeholder substituted per skill.
- test_erp_client.py: appends any core test class the persona copy is
  missing (matched by class name); never touches existing classes.
- connector-reference.md: replaces/adds matching `##` sections by heading
  text; sections unique to a persona copy (e.g. "Live validation record",
  "Extension point", "Known gaps") are preserved untouched, wherever they
  are in the file. The preamble (before the first `##`) is left alone.

Run with --dry-run first (default) to see a summary of what would change
per skill before writing anything. Pass --apply to actually write.
"""

import argparse
import re
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = CORE_DIR.parent

PERSONA_SKILLS = [
    "qkeee-erp-accounts-executive", "qkeee-erp-bot-init",
    "qkeee-erp-fixed-asset-manager", "qkeee-erp-hr-associate", "qkeee-erp-inventory",
    "qkeee-erp-mis-analyst", "qkeee-erp-procurement", "qkeee-erp-sales",
    "qkeee-erp-system-admin",
]

CONFIRM_TOKEN_SKILLS = [
    "qkeee-erp-bot-init", "qkeee-erp-fixed-asset-manager",
    "qkeee-erp-system-admin",
]

# READ_ONLY_SKILLS and WRITE_PATH_FUNCTIONS used to be hand-maintained
# lists here — that's exactly what silently broke qkeee-erp-mis-analyst
# once already (2026-08-18 postmortem, see module docstring): nothing
# forced this file to stay in sync with which skills/functions actually
# are read-only/write-path. Both are now derived from checkable markers
# in the source files themselves — see _derive_write_path_functions()
# and _derive_read_only_skills() below. `# qkeee-erp:write-path` sits on
# the line immediately above each write-path `def` in core's
# erp_client.py; `# qkeee-erp:read-only-skill` sits near the top of a
# persona's own erp_client.py. Sync must never (re)introduce a
# write-path function into a read-only-marked skill, even though it's
# "missing" by the normal missing-in-target-but-present-in-core logic.

# Every top-level function in core's erp_client.py that is shared connector
# plumbing, never persona-specific business logic. `_cli` is intentionally
# excluded — see module docstring.
SHARED_FUNCTIONS = [
    "_tag_env_var", "_parse_bool_env", "get_env_config", "_request", "health_check",
    "query_resource", "_strip_noise", "get_resource", "resource_exists",
    "run_query_report", "get_user_roles", "record_comment", "_now_iso",
    "_session_or_fallback", "_diff_fields", "_audit_insert", "_audit_update",
    "_audit_submit", "_log_read", "record_audit_log_start",
    "record_audit_log_finish", "ensure_persona_registered", "mutate_resource",
    "_do_mutate", "list_configured_tags", "discover_harness_http_tool",
]

CONFIRM_TOKEN_SHARED_FUNCTIONS = ["compute_token", "is_fresh"]
CORE_TEST_CLASSES_TO_APPEND = ["RunQueryReportTests", "GetUserRolesTests"]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _derive_write_path_functions(core_src: str) -> set:
    """Functions marked `# qkeee-erp:write-path` on the line immediately
    above their `def` in core's erp_client.py. Checkable directly from
    the source instead of a hand-maintained list — see the module
    docstring's mis-analyst postmortem."""
    marker = "# qkeee-erp:write-path"
    lines = core_src.split("\n")
    names = set()
    for i, line in enumerate(lines):
        if line.strip() != marker:
            continue
        for j in range(i + 1, len(lines)):
            stripped = lines[j].strip()
            if not stripped:
                continue
            m = re.match(r"def (\w+)\(", stripped)
            if m:
                names.add(m.group(1))
            break
    return names


def _derive_read_only_skills() -> list:
    """Persona skills whose own erp_client.py carries a
    `# qkeee-erp:read-only-skill` marker near the top. Checkable
    directly from each persona's file instead of a hand-maintained
    list — see the module docstring's mis-analyst postmortem."""
    marker = "# qkeee-erp:read-only-skill"
    out = []
    for skill in PERSONA_SKILLS:
        p = SKILLS_DIR / skill / "scripts" / "erp_client.py"
        if p.exists() and marker in read(p):
            out.append(skill)
    return out


def split_py_blocks(src: str, keyword: str):
    """Split source into an ordered list of blocks:
      {"type": "code", "lines": [...]}                       — anything else
      {"type": keyword-match, "name": str, "lines": [...]}    — a top-level
                                                                 `def NAME(` or
                                                                 `class NAME` block

    A block of the matched type continues consuming lines as long as each
    line is blank or indented (still inside the def/class body). The first
    non-indented, non-blank line ends it — that line starts a new "code"
    block (this correctly preserves multi-line top-level constants that sit
    between two functions, e.g. a dict literal, instead of truncating them).
    """
    pattern = re.compile(rf"^{keyword} (\w+)")
    blocks = []
    current = {"type": "code", "lines": []}

    def flush():
        if current["lines"]:
            blocks.append(current)

    for line in src.split("\n"):
        m = pattern.match(line)
        if m:
            flush()
            current = {"type": "match", "name": m.group(1), "lines": [line]}
        elif current["type"] == "match" and (line == "" or line[0].isspace()):
            current["lines"].append(line)
        elif current["type"] == "match":
            flush()
            current = {"type": "code", "lines": [line]}
        else:
            current["lines"].append(line)
    flush()
    return blocks


def blocks_by_name(blocks):
    return {b["name"]: b for b in blocks if b["type"] == "match"}


def merge_py(core_src: str, target_src: str, shared_names: list, keyword: str,
             appendable_names: list = None) -> tuple:
    """`shared_names` controls the update-if-present pass (a block already
    in the target under one of these names always gets replaced with
    core's version, or left alone if identical). `appendable_names`
    (defaults to `shared_names`) separately controls the missing-in-
    target -> append pass — pass a narrower set here for a target that
    deliberately omits some shared-named functions entirely, so "missing"
    doesn't get silently reinterpreted as "never built yet, add it" (see
    module docstring's mis-analyst postmortem)."""
    if appendable_names is None:
        appendable_names = shared_names
    core_blocks = blocks_by_name(split_py_blocks(core_src, keyword))
    target_blocks = split_py_blocks(target_src, keyword)
    target_by_name = blocks_by_name(target_blocks)

    report = {"updated": [], "added": [], "persona_only_kept": [], "unchanged": []}

    new_blocks = []
    for b in target_blocks:
        if b["type"] == "match" and b["name"] in shared_names and b["name"] in core_blocks:
            core_block = core_blocks[b["name"]]
            if core_block["lines"] != b["lines"]:
                report["updated"].append(b["name"])
            else:
                report["unchanged"].append(b["name"])
            new_blocks.append(core_block)
        else:
            if b["type"] == "match" and b["name"] not in shared_names:
                report["persona_only_kept"].append(b["name"])
            new_blocks.append(b)

    missing = [name for name in appendable_names if name in core_blocks and name not in target_by_name]
    to_append = [core_blocks[name] for name in missing]
    report["added"] = missing

    if to_append:
        # Insert before a trailing `if __name__ == "__main__":` guard block, if any.
        guard_idx = None
        for i, b in enumerate(new_blocks):
            if b["type"] == "code" and any(l.startswith('if __name__') for l in b["lines"]):
                guard_idx = i
        insertion = []
        for blk in to_append:
            insertion.append({"type": "code", "lines": ["", ""]})
            insertion.append(blk)
        if guard_idx is not None:
            new_blocks = new_blocks[:guard_idx] + insertion + new_blocks[guard_idx:]
        else:
            new_blocks = new_blocks + insertion

    merged_lines = []
    for b in new_blocks:
        merged_lines.extend(b["lines"])
    merged = "\n".join(merged_lines)
    if not merged.endswith("\n"):
        merged += "\n"
    return merged, report


def split_md_sections(src: str):
    lines = src.split("\n")
    preamble = []
    sections = {}
    order = []
    cur_heading = None
    cur_lines = []

    def flush():
        if cur_heading is not None:
            sections[cur_heading] = "\n".join(cur_lines)

    for line in lines:
        m = re.match(r"^## (.+)$", line)
        if m:
            flush()
            cur_heading = m.group(1).strip()
            order.append(cur_heading)
            cur_lines = [line]
            continue
        if cur_heading is None:
            preamble.append(line)
        else:
            cur_lines.append(line)
    flush()
    return "\n".join(preamble), sections, order


def merge_md(core_src: str, target_src: str) -> tuple:
    _, core_sections, core_order = split_md_sections(core_src)
    target_preamble, target_sections, target_order = split_md_sections(target_src)

    report = {"updated": [], "added": [], "persona_only_kept": []}
    new_sections = dict(target_sections)
    new_order = list(target_order)

    for heading in core_order:
        core_body = core_sections[heading]
        if heading in target_sections:
            if target_sections[heading] != core_body:
                report["updated"].append(heading)
            new_sections[heading] = core_body
        else:
            report["added"].append(heading)
            new_sections[heading] = core_body
            new_order.append(heading)

    for heading in target_order:
        if heading not in core_sections:
            report["persona_only_kept"].append(heading)

    body = "\n\n".join(new_sections[h].rstrip("\n") for h in new_order)
    merged = target_preamble.rstrip("\n") + "\n\n" + body.rstrip("\n") + "\n"
    return merged, report


def sync_erp_client(skill: str, apply: bool) -> dict:
    core_path = CORE_DIR / "scripts" / "erp_client.py"
    target_path = SKILLS_DIR / skill / "scripts" / "erp_client.py"
    if not target_path.exists():
        return {"skipped": "no erp_client.py in target"}
    core_src = read(core_path)
    appendable = SHARED_FUNCTIONS
    if skill in _derive_read_only_skills():
        write_path_functions = _derive_write_path_functions(core_src)
        appendable = [n for n in SHARED_FUNCTIONS if n not in write_path_functions]
    merged, report = merge_py(core_src, read(target_path), SHARED_FUNCTIONS, "def",
                               appendable_names=appendable)
    if apply:
        target_path.write_text(merged, encoding="utf-8", newline="\n")
    return report


def sync_confirm_token(skill: str, apply: bool) -> dict:
    core_path = CORE_DIR / "scripts" / "confirm_token.py"
    target_path = SKILLS_DIR / skill / "scripts" / "confirm_token.py"
    if not target_path.exists():
        return {"skipped": "no confirm_token.py in target"}
    merged, report = merge_py(read(core_path), read(target_path), CONFIRM_TOKEN_SHARED_FUNCTIONS, "def")
    if apply:
        target_path.write_text(merged, encoding="utf-8", newline="\n")
    return report


def sync_discover(skill: str, apply: bool) -> dict:
    core_script = read(CORE_DIR / "scripts" / "discover.py")
    core_test = read(CORE_DIR / "scripts" / "test_discover.py")
    persona_script = core_script.replace("qkeee-erp-frappe-core discovery helper", f"{skill} discovery helper") \
                                 .replace('default="qkeee-erp-frappe-core"', f'default="{skill}"')
    target_script = SKILLS_DIR / skill / "scripts" / "discover.py"
    target_test = SKILLS_DIR / skill / "scripts" / "test_discover.py"
    existed = target_script.exists()
    # Compare against the persona-substituted content, not just existence —
    # a byte-for-byte match must report no drift, or --check would flag
    # every persona on every run regardless of whether anything changed.
    script_changed = (not existed) or read(target_script) != persona_script
    test_changed = (not target_test.exists()) or read(target_test) != core_test
    if apply:
        target_script.write_text(persona_script, encoding="utf-8", newline="\n")
        target_test.write_text(core_test, encoding="utf-8", newline="\n")
    return {"created": not existed, "updated": existed and (script_changed or test_changed)}


def sync_test_erp_client(skill: str, apply: bool) -> dict:
    core_path = CORE_DIR / "scripts" / "test_erp_client.py"
    target_path = SKILLS_DIR / skill / "scripts" / "test_erp_client.py"
    if not target_path.exists():
        return {"skipped": "no test_erp_client.py in target"}
    core_classes = blocks_by_name(split_py_blocks(read(core_path), "class"))
    target_src = read(target_path)
    target_classes = blocks_by_name(split_py_blocks(target_src, "class"))

    to_append = [name for name in CORE_TEST_CLASSES_TO_APPEND
                 if name in core_classes and name not in target_classes]
    if not to_append:
        return {"added": []}

    marker = '\nif __name__ == "__main__":'
    addition_text = "\n\n\n".join(
        "\n".join(core_classes[name]["lines"]).rstrip("\n") for name in to_append
    )
    # These appended classes reference the bare `erp_client` module name and
    # `unittest.mock.patch.object` (fully-qualified, not `ec`/`patch`
    # aliases) so they work regardless of how the target file's own header
    # imports erp_client (some skills do `from erp_client import (...)`,
    # which doesn't bind the module name itself). Redundant if the target
    # already imports these some other way — Python allows re-imports.
    addition = (
        "\n\n"
        "import erp_client\n"
        "import unittest.mock\n\n\n"
        + addition_text + "\n"
    )
    if marker in target_src:
        merged = target_src.replace(marker, addition + marker, 1)
    else:
        merged = target_src.rstrip("\n") + addition
    if apply:
        target_path.write_text(merged, encoding="utf-8", newline="\n")
    return {"added": to_append}


def sync_connector_reference(skill: str, apply: bool) -> dict:
    core_path = CORE_DIR / "references" / "connector-reference.md"
    target_path = SKILLS_DIR / skill / "references" / "connector-reference.md"
    if not target_path.exists():
        return {"skipped": "no connector-reference.md in target"}
    merged, report = merge_md(read(core_path), read(target_path))
    if apply:
        target_path.write_text(merged, encoding="utf-8", newline="\n")
    return report


def _drifted(report: dict) -> bool:
    """True if a sync_*() report shows any pending change (excludes
    persona_only_kept/unchanged, which are expected steady state)."""
    return bool(report.get("updated") or report.get("added") or
                report.get("created"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run, report only)")
    ap.add_argument("--skill", action="append", help="limit to one or more skills (repeatable); default: all")
    ap.add_argument("--check", action="store_true",
                     help="dry-run and exit non-zero if any persona has drifted from core "
                          "(no files written even if combined with --apply). For CI.")
    args = ap.parse_args()

    skills = args.skill or PERSONA_SKILLS
    apply = args.apply and not args.check
    any_drift = False

    for skill in skills:
        print(f"\n{'='*80}\n{skill}\n{'='*80}")

        r = sync_erp_client(skill, apply)
        print(f"erp_client.py: {r}")
        any_drift = any_drift or _drifted(r)

        r = sync_test_erp_client(skill, apply)
        print(f"test_erp_client.py: {r}")
        any_drift = any_drift or _drifted(r)

        r = sync_discover(skill, apply)
        print(f"discover.py/test_discover.py: {r}")
        any_drift = any_drift or _drifted(r)

        if skill in CONFIRM_TOKEN_SKILLS:
            r = sync_confirm_token(skill, apply)
            print(f"confirm_token.py: {r}")
            any_drift = any_drift or _drifted(r)

        r = sync_connector_reference(skill, apply)
        print(f"connector-reference.md: {r}")
        any_drift = any_drift or _drifted(r)

    if args.check:
        if any_drift:
            print("\n\nDRIFT DETECTED — one or more personas are out of sync with "
                  "qkeee-erp-frappe-core. Run `python sync_to_personas.py --apply` "
                  "and commit the result.")
            raise SystemExit(1)
        print("\n\nNo drift — all personas in sync with qkeee-erp-frappe-core.")
    elif not apply:
        print("\n\nDRY RUN — no files written. Re-run with --apply to write changes.")


if __name__ == "__main__":
    main()
