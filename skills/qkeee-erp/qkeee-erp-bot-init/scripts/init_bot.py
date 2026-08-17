#!/usr/bin/env python3
"""
qkeee-erp-bot-init — idempotent provisioning of the Qkeee Bot audit-trail
doctypes (+ role) into a target ERPNext instance.

Checks for the Qkeee Bot role and the 4 Qkeee Bot * doctypes; creates
whatever's missing via the generic DocType/Role create path. Safe to
re-run — every step is existence-checked first.

Requires an ELEVATED (System Manager/Administrator) API key for the
target tag — creating DocType/Role records needs permission the shared
qkeee-erp-bot@<org> steady-state service account should not hold. See
references/bot-doctypes-design.md.

Two-step flow, code-enforced (not just prompt-instructed — see
confirm_token.py):
  1. --dry-run prints the plan (what would be created) plus a
     --confirm-token/--issued-at pair.
  2. A real run must pass that exact --confirm-token/--issued-at back.
     init_bot recomputes the token from the *current* live plan and
     refuses to proceed if it doesn't match (target state changed since
     the dry-run, or the token/timestamp was tampered with/is stale) or
     if the token is older than confirm_token.DEFAULT_TOKEN_TTL_SECONDS.
     A real run with nothing left to create needs no token (nothing to
     confirm).

Usage:
    python init_bot.py --tag qa --requested-by admin@org.com --dry-run
    python init_bot.py --tag qa --requested-by admin@org.com \\
        --confirm-token <token> --issued-at <epoch>
"""

import argparse
import json
import sys
import time

import erp_client
from confirm_token import init_plan_token, is_fresh, DEFAULT_TOKEN_TTL_SECONDS
from doctype_defs import ALL_DOCTYPES, DEFERRED_FIELD_PATCHES, ROLE_NAME, ROLE_PAYLOAD


def _step(label):
    print(f"--- {label} ---")


def compute_plan(tag: str) -> dict:
    """Existence-checks the role and every doctype against the live target
    right now. Returns what's actually needed — this is re-run fresh on
    every dry-run AND every real run, so a real run's token check is
    always comparing against current target state, not a cached plan."""
    role_needed = not erp_client.resource_exists(tag, "Role", ROLE_NAME)
    doctypes_needed = [
        d["name"] for d in ALL_DOCTYPES
        if not erp_client.resource_exists(tag, "DocType", d["name"])
    ]
    return {"role_needed": role_needed, "doctypes_needed": doctypes_needed}


def ensure_role(tag: str, requested_by: str, approval_note: str) -> bool:
    """Returns True if the role was created, False if it already existed."""
    if erp_client.resource_exists(tag, "Role", ROLE_NAME):
        print(f"Role '{ROLE_NAME}' already exists — skipping.")
        return False
    erp_client.mutate_resource(
        tag, "Role", "create", payload=ROLE_PAYLOAD,
        mode="read-write", requested_by=requested_by,
        user_approved=True, approval_note=approval_note,
    )
    print(f"Created Role '{ROLE_NAME}'.")
    return True


def ensure_doctype(tag: str, doctype_def: dict, requested_by: str, approval_note: str) -> bool:
    name = doctype_def["name"]
    if erp_client.resource_exists(tag, "DocType", name):
        print(f"DocType '{name}' already exists — skipping.")
        return False
    erp_client.mutate_resource(
        tag, "DocType", "create", payload=doctype_def,
        mode="read-write", requested_by=requested_by,
        user_approved=True, approval_note=approval_note,
    )
    print(f"Created DocType '{name}'.")
    return True


def field_exists(tag: str, doctype_name: str, fieldname: str) -> bool:
    """Whether a live DocType's current field list already has `fieldname`
    — used to check deferred-field patches idempotently (not just whether
    the doctype exists, but whether this specific field has landed yet)."""
    doc = erp_client.get_resource(tag, "DocType", doctype_name).get("data")
    if not doc:
        return False
    return any(f.get("fieldname") == fieldname for f in doc.get("fields", []))


def ensure_deferred_fields(tag: str, requested_by: str, approval_note: str) -> list:
    """Patches in any DEFERRED_FIELD_PATCHES field whose target doctype
    (`requires`) now exists but hasn't been applied yet. Live-confirmed
    2026-08-16: Frappe rejects a Link field naming a not-yet-existing
    doctype at DocType-create time, so Qkeee Bot Message's
    linked_audit_log field (-> Qkeee Bot Audit Log, created after Message
    in ALL_DOCTYPES order) can't be part of Message's initial create
    payload — it's added here instead, once Audit Log exists. Bundled
    into the same confirmed run as the doctype creation itself (the user
    already confirmed creating Message's schema; completing that schema
    with its one deferred field is the same scope, not a new action), so
    it doesn't need its own separate confirm-token round trip. Safe to
    call on every run — no-ops once every deferred field has landed."""
    patched = []
    for patch in DEFERRED_FIELD_PATCHES:
        dt, fieldname, requires = patch["doctype"], patch["field"]["fieldname"], patch["requires"]
        if not erp_client.resource_exists(tag, "DocType", dt):
            continue  # nothing to patch — dt itself doesn't exist (yet)
        if not erp_client.resource_exists(tag, "DocType", requires):
            print(f"Deferred field '{dt}.{fieldname}' still waiting on '{requires}' — skipping for now.")
            continue
        if field_exists(tag, dt, fieldname):
            print(f"Field '{dt}.{fieldname}' already present — skipping.")
            continue
        doc = erp_client.get_resource(tag, "DocType", dt)["data"]
        new_fields = list(doc.get("fields", [])) + [patch["field"]]
        erp_client.mutate_resource(
            tag, "DocType", "update", payload={"fields": new_fields}, name=dt,
            mode="read-write", requested_by=requested_by,
            user_approved=True, approval_note=approval_note,
        )
        print(f"Patched '{dt}' with deferred field '{fieldname}'.")
        patched.append(f"{dt}.{fieldname}")
    return patched


def run_dry_run(tag: str, requested_by: str) -> dict:
    _step("Health check")
    print(json.dumps(erp_client.health_check(tag), indent=2))

    _step("Plan")
    plan = compute_plan(tag)
    if not plan["role_needed"] and not plan["doctypes_needed"]:
        print("Nothing to do — role and all 4 doctypes already exist.")
        return {"tag": tag, "dry_run": True, **plan, "confirm_token": None, "issued_at": None}

    issued_at = int(time.time())
    token = init_plan_token(tag, requested_by, plan["role_needed"], plan["doctypes_needed"], issued_at)

    if plan["role_needed"]:
        print(f"[dry-run] Would create Role '{ROLE_NAME}'.")
    for name in plan["doctypes_needed"]:
        doctype_def = next(d for d in ALL_DOCTYPES if d["name"] == name)
        print(f"[dry-run] Would create DocType '{name}' with "
              f"{len(doctype_def['fields'])} fields, "
              f"{len(doctype_def['permissions'])} permission rows.")
    for patch in DEFERRED_FIELD_PATCHES:
        print(f"[dry-run] Would also patch in deferred field "
              f"'{patch['doctype']}.{patch['field']['fieldname']}' once "
              f"'{patch['requires']}' exists (can't be in the initial create "
              f"payload — see doctype_defs.py).")

    _step("Confirm token")
    print(f"To run this for real, re-invoke with:\n"
          f"  --confirm-token {token} --issued-at {issued_at}\n"
          f"Valid for {DEFAULT_TOKEN_TTL_SECONDS // 60} minutes from now. "
          f"Only pass this back after the user has explicitly confirmed "
          f"the plan printed above.")
    return {"tag": tag, "dry_run": True, **plan, "confirm_token": token, "issued_at": issued_at}


def run_real(tag: str, requested_by: str, confirm_token: str, issued_at: int) -> dict:
    _step("Health check")
    print(json.dumps(erp_client.health_check(tag), indent=2))

    _step("Plan (recomputed against current target state)")
    plan = compute_plan(tag)

    if plan["role_needed"] or plan["doctypes_needed"]:
        if not confirm_token or issued_at is None:
            raise erp_client.ConnectorError(
                "This run would create records but no --confirm-token/--issued-at was "
                "given. Run with --dry-run first, show the plan to the user, and only "
                "re-invoke with the token it prints after they explicitly confirm."
            )
        if not is_fresh(issued_at):
            raise erp_client.ConnectorError(
                f"--issued-at is stale or invalid (must be within "
                f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes). Run --dry-run again to get "
                f"a fresh token."
            )
        expected = init_plan_token(tag, requested_by, plan["role_needed"], plan["doctypes_needed"], issued_at)
        if expected != confirm_token:
            raise erp_client.ConnectorError(
                "--confirm-token does not match the current plan for this target. Either "
                "the target's state changed since the dry-run (something was created/"
                "removed out of band), or requested_by/tag differs from the dry-run. "
                "Run --dry-run again and re-confirm with the new token."
            )

    approval_note = f"dry-run confirmed by {requested_by}, token {confirm_token[:8]}..." if confirm_token else \
        "no records needed creating — nothing to confirm"

    _step(f"Role: {ROLE_NAME}")
    role_created = ensure_role(tag, requested_by, approval_note)

    results = {}
    for doctype_def in ALL_DOCTYPES:
        _step(f"DocType: {doctype_def['name']}")
        results[doctype_def["name"]] = ensure_doctype(tag, doctype_def, requested_by, approval_note)

    _step("Deferred field patches")
    deferred_fields_patched = ensure_deferred_fields(tag, requested_by, approval_note)

    _step("Summary")
    summary = {
        "tag": tag,
        "dry_run": False,
        "role_created": role_created,
        "doctypes_created": [name for name, created in results.items() if created],
        "doctypes_already_present": [name for name, created in results.items() if not created],
        "deferred_fields_patched": deferred_fields_patched,
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
    except erp_client.ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
