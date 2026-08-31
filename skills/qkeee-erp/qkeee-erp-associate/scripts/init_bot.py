#!/usr/bin/env python3
"""
qkeee-erp-associate — init_bot (admin-invoked, one-time provisioning helper).

Per the consolidation plan section 7: "Bot-init's provisioning script
survives as scripts/init_bot.py inside the unified skill — an admin-
invoked, one-time action, not part of the associate's normal
conversational flow." Not a domain module and not part of the associate's
normal activation sequence — a human/admin runs this deliberately, once
per target environment (or after a schema change), same posture as the
old `qkeee-erp-bot-init` skill it's ported from.

Phase 1 (connector consolidation) carried over only this file's single
`erp_client.py`-derived function, `ensure_qkeee_env_file_skeleton()`.
Phase 3 (doctype migration, this pass) ports the rest of
`qkeee-erp-bot-init/scripts/init_bot.py`'s provisioning flow — WITH the
persona manifest/registration step dropped entirely per plan §7's
disposition table (`Qkeee ERP Bot Persona` is removed; see
`../CHANGELOG.md`), and Audit Log's `persona_code` field renamed to
`domain_code` in `doctype_defs.py`.

**Deliberately narrower than the old init_bot.py in one more way**: this
version provisions ONLY the `Qkeee Bot` Role and the `Qkeee Bot Audit Log`
DocType — no `--bot-email`/bot-user provisioning path (that lived in
`ensure_bot_user.py`, which has not been ported into this skill; run the
old `qkeee-erp-bot-init/scripts/ensure_bot_user.py` directly if bot-user
provisioning is needed, until a future pass folds it in here too). Scoped
this way deliberately for Phase 3, which is doctype-code migration only.

**CODE-ONLY as of this commit** — this script has not been run against
any live ERPNext instance in this form. The dry-run/confirm-token
discipline below is unchanged from the pre-Phase-3 version: nothing here
executes a write without a prior --dry-run's token, and a real run
recomputes its plan against the target's actual current state before
trusting a passed-in token.

Requires an ELEVATED (System Manager/Administrator) API key for the
target tag — creating a DocType/Role record needs permission the shared
qkeee-erp-bot@<org> steady-state service account should not hold.

Usage:
    python init_bot.py --tag qa --requested-by admin@org.com --dry-run
    python init_bot.py --tag qa --requested-by admin@org.com \\
        --confirm-token <token> --issued-at <epoch>
"""

import argparse
import json
import os
import sys
import time

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client
from core.client import ConnectorError, _qkeee_env_file_path
from core.confirm_token import compute_token, is_fresh, DEFAULT_TOKEN_TTL_SECONDS
from doctype_defs import ALL_DOCTYPES, ROLE_NAME, ROLE_PAYLOAD


def ensure_qkeee_env_file_skeleton() -> bool:
    """Create qkeee-erp.env with a header comment ONLY (no tag lines, no
    secrets) if it doesn't already exist. Returns True if it was created,
    False if it was already there.

    Deliberately does NOT write BASE_URL/API_KEY/API_SECRET into this
    file, even when this process has them (e.g. from os.environ) —
    credentials only ever land in this file via the operator's own manual
    copy-paste from a one-time stdout print (see
    qkeee-erp-bot-init/scripts/ensure_bot_user.py, not yet ported here),
    never by this tooling reading them back and re-writing them. This
    function exists only so a user running init_bot on a truly fresh
    profile isn't left hunting for where to put the lines it tells them
    to add — it's the empty-file convenience half of that instruction,
    not a partial reversal of it."""
    path = _qkeee_env_file_path()
    if os.path.exists(path):
        return False
    header = (
        "# qkeee-erp.env — ERPNext credentials for the qkeee-erp-associate skill.\n"
        "# Created by init_bot.py. Add three lines per environment tag:\n"
        "#   QKEEE_ERP_<TAG>_BASE_URL=https://org.erpnext.com\n"
        "#   QKEEE_ERP_<TAG>_API_KEY=...\n"
        "#   QKEEE_ERP_<TAG>_API_SECRET=...\n"
        "# Optional per-tag: QKEEE_ERP_<TAG>_DEBUG, QKEEE_ERP_<TAG>_REQUESTED_BY.\n"
        "# Never committed, never read back by this tooling — you paste values in\n"
        "# yourself, once, after generating/rotating a key.\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
    return True


def _init_plan_token(tag: str, requested_by: str, role_needed: bool,
                      doctypes_needed: list, issued_at: int = None) -> str:
    """Token over the (slimmed, persona-free) init plan: which tag, who's
    running it, whether the Role needs creating, and exactly which
    doctype names need creating (sorted, so ordering never causes a
    spurious mismatch). issued_at (epoch seconds) is required — it's the
    dry-run timestamp, baked into the token so it can't outlive
    DEFAULT_TOKEN_TTL_SECONDS (checked separately via is_fresh(), not by
    this function).

    Ported/renamed from qkeee-erp-bot-init/scripts/confirm_token.py's
    init_plan_token() — that file's full_init_plan_token() (which also
    covered personas_needed/bot-user fields) is NOT ported here, since
    this skill's init_bot.py dropped both the persona and bot-user
    provisioning paths (see this module's docstring and
    ../CHANGELOG.md)."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the dry-run-time epoch seconds.")
    return compute_token(
        kind="qkeee_erp_associate_init_plan",
        tag=tag,
        requested_by=requested_by,
        role_needed=bool(role_needed),
        doctypes_needed=sorted(doctypes_needed),
        issued_at=int(issued_at),
    )


def _step(label):
    print(f"--- {label} ---")


def compute_plan(tag: str) -> dict:
    """Existence-checks the Role and the (now-single) Audit Log doctype
    against the live target right now. Returns what's actually needed —
    re-run fresh on every dry-run AND every real run, so a real run's
    token check always compares against current target state, not a
    cached plan."""
    role_needed = not core_client.resource_exists(tag, "Role", ROLE_NAME)
    doctypes_needed = [
        d["name"] for d in ALL_DOCTYPES
        if not core_client.resource_exists(tag, "DocType", d["name"])
    ]
    return {"role_needed": role_needed, "doctypes_needed": doctypes_needed}


def ensure_role(tag: str, requested_by: str, approval_note: str) -> bool:
    """Returns True if the role was created, False if it already existed."""
    if core_client.resource_exists(tag, "Role", ROLE_NAME):
        print(f"Role '{ROLE_NAME}' already exists — skipping.")
        return False
    core_client.mutate_resource(
        tag, "Role", "create", payload=ROLE_PAYLOAD,
        mode="read-write", requested_by=requested_by,
        user_approved=True, approval_note=approval_note,
    )
    print(f"Created Role '{ROLE_NAME}'.")
    return True


def ensure_doctype(tag: str, doctype_def: dict, requested_by: str, approval_note: str) -> bool:
    name = doctype_def["name"]
    if core_client.resource_exists(tag, "DocType", name):
        print(f"DocType '{name}' already exists — skipping.")
        return False
    core_client.mutate_resource(
        tag, "DocType", "create", payload=doctype_def,
        mode="read-write", requested_by=requested_by,
        user_approved=True, approval_note=approval_note,
    )
    print(f"Created DocType '{name}'.")
    return True


def run_dry_run(tag: str, requested_by: str) -> dict:
    _step("Health check")
    print(json.dumps(core_client.health_check(tag), indent=2))

    _step("Plan")
    plan = compute_plan(tag)
    nothing_needed = not plan["role_needed"] and not plan["doctypes_needed"]
    if nothing_needed:
        print("Nothing to do — role and doctype already in place.")
        return {"tag": tag, "dry_run": True, **plan, "confirm_token": None, "issued_at": None}

    issued_at = int(time.time())
    token = _init_plan_token(tag, requested_by, plan["role_needed"], plan["doctypes_needed"],
                              issued_at=issued_at)

    if plan["role_needed"]:
        print(f"[dry-run] Would create Role '{ROLE_NAME}'.")
    for name in plan["doctypes_needed"]:
        doctype_def = next(d for d in ALL_DOCTYPES if d["name"] == name)
        print(f"[dry-run] Would create DocType '{name}' with "
              f"{len(doctype_def['fields'])} fields, "
              f"{len(doctype_def['permissions'])} permission rows.")

    _step("Confirm token")
    print(f"To run this for real, re-invoke with:\n"
          f"  --confirm-token {token} --issued-at {issued_at}\n"
          f"Valid for {DEFAULT_TOKEN_TTL_SECONDS // 60} minutes from now. "
          f"Only pass this back after the user has explicitly confirmed "
          f"the plan printed above.")
    return {"tag": tag, "dry_run": True, **plan, "confirm_token": token, "issued_at": issued_at}


def run_real(tag: str, requested_by: str, confirm_token: str, issued_at: int) -> dict:
    _step("Health check")
    print(json.dumps(core_client.health_check(tag), indent=2))

    _step("Plan (recomputed against current target state)")
    plan = compute_plan(tag)

    action_needed = plan["role_needed"] or plan["doctypes_needed"]
    if action_needed:
        if not confirm_token or issued_at is None:
            raise ConnectorError(
                "This run would create/change records but no --confirm-token/--issued-at "
                "was given. Run with --dry-run first, show the plan to the user, and only "
                "re-invoke with the token it prints after they explicitly confirm."
            )
        if not is_fresh(issued_at):
            raise ConnectorError(
                f"--issued-at is stale or invalid (must be within "
                f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes). Run --dry-run again to get "
                f"a fresh token."
            )
        expected = _init_plan_token(tag, requested_by, plan["role_needed"],
                                     plan["doctypes_needed"], issued_at=issued_at)
        if expected != confirm_token:
            raise ConnectorError(
                "--confirm-token does not match the current plan for this target. Either "
                "the target's state changed since the dry-run (something was created/"
                "removed out of band), or requested_by/tag differs from the dry-run. Run "
                "--dry-run again and re-confirm with the new token."
            )

    approval_note = (f"dry-run confirmed by {requested_by}, token {confirm_token[:8]}..."
                      if confirm_token else "no records needed creating — nothing to confirm")

    _step(f"Role: {ROLE_NAME}")
    role_created = ensure_role(tag, requested_by, approval_note)

    results = {}
    for doctype_def in ALL_DOCTYPES:
        _step(f"DocType: {doctype_def['name']}")
        results[doctype_def["name"]] = ensure_doctype(tag, doctype_def, requested_by, approval_note)

    _step("qkeee-erp.env")
    env_created = ensure_qkeee_env_file_skeleton()
    print(f"{'Created empty' if env_created else 'Found existing'} "
          f"{_qkeee_env_file_path()}.")

    _step("Summary")
    summary = {
        "tag": tag,
        "dry_run": False,
        "role_created": role_created,
        "doctypes_created": [name for name, created in results.items() if created],
        "doctypes_already_present": [name for name, created in results.items() if not created],
        "qkeee_env_file_created": env_created,
    }
    print(json.dumps(summary, indent=2))
    return summary


def _cli():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tag", required=True, help="environment tag, from qkeee_erp.active_env")
    p.add_argument("--requested-by", required=True,
                   help="ERPNext user id/email running this init (elevated/admin account)")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be created, and print a confirm-token, without writing anything")
    p.add_argument("--confirm-token", help="token printed by a prior --dry-run")
    p.add_argument("--issued-at", type=int, help="issued_at epoch seconds printed by a prior --dry-run")
    args = p.parse_args()

    try:
        if args.dry_run:
            run_dry_run(args.tag, args.requested_by)
        else:
            run_real(args.tag, args.requested_by, args.confirm_token, args.issued_at)
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
