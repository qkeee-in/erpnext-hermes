#!/usr/bin/env python3
"""
qkeee-erp-bot-init — idempotent provisioning of the Qkeee Bot audit-trail
doctypes (+ role) into a target ERPNext instance.

Checks for the Qkeee Bot role and the 2 Qkeee Bot * doctypes; creates
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

import ensure_bot_user
import erp_client
from confirm_token import full_init_plan_token, is_fresh, DEFAULT_TOKEN_TTL_SECONDS
from doctype_defs import ALL_DOCTYPES, PERSONA_MANIFEST, ROLE_NAME, ROLE_PAYLOAD


def _step(label):
    print(f"--- {label} ---")


def compute_plan(tag: str, bot_email: str = None) -> dict:
    """Existence-checks the role, every doctype, every manifest persona,
    and (if bot_email given) the bot-user, against the live target right
    now. Returns what's actually needed — this is re-run fresh on every
    dry-run AND every real run, so a real run's token check is always
    comparing against current target state, not a cached plan.

    personas_needed is checked via PERSONA_DOCTYPE existence — on a truly
    fresh instance the doctype itself doesn't exist yet, so
    resource_exists() (404-tolerant) reports every manifest persona as
    needed, which is correct: they'll all get created once the doctype
    does, in the same real-run.

    The bot-user sub-plan is computed with require_role_exists=False —
    the Role may not exist yet at dry-run time (it's created earlier in
    the same real-run this plan gates), so the same missing-role state
    that would make ensure_bot_user.py hard-fail standalone just folds
    into role_needed here instead. See that function's docstring."""
    role_needed = not erp_client.resource_exists(tag, "Role", ROLE_NAME)
    doctypes_needed = [
        d["name"] for d in ALL_DOCTYPES
        if not erp_client.resource_exists(tag, "DocType", d["name"])
    ]
    personas_needed = [
        p["persona_code"] for p in PERSONA_MANIFEST
        if not erp_client.resource_exists(tag, "Qkeee Bot Persona", p["persona_code"])
    ]
    plan = {
        "role_needed": role_needed, "doctypes_needed": doctypes_needed,
        "personas_needed": personas_needed,
    }
    if bot_email:
        user_plan = ensure_bot_user.compute_plan(tag, bot_email, require_role_exists=False)
        plan.update({
            "bot_email": bot_email,
            "user_needed": user_plan["user_needed"],
            "user_role_needed": user_plan["role_needed"],
            "enable_needed": user_plan["enable_needed"],
            "keys_needed": user_plan["keys_needed"],
        })
    else:
        plan.update({"bot_email": None, "user_needed": False, "user_role_needed": False,
                     "enable_needed": False, "keys_needed": False})
    return plan


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


def ensure_personas(tag: str, requested_by: str) -> dict:
    """Registers every persona in PERSONA_MANIFEST that isn't already a
    Qkeee Bot Persona row — unconditional, best-effort per-persona
    (ensure_persona_registered() never raises), same as a persona skill
    self-registering on its own first use, just done for every shipped
    persona up front instead of waiting on each one's own first session."""
    results = {}
    for p in PERSONA_MANIFEST:
        status = erp_client.ensure_persona_registered(
            tag, persona_code=p["persona_code"], persona_label=p["persona_label"],
            default_mode="read-only", requested_by=requested_by,
        )
        print(f"Persona '{p['persona_code']}': {status}.")
        results[p["persona_code"]] = status
    return results


def ensure_bot_user_step(tag: str, bot_email: str, requested_by: str, approval_note: str,
                          plan: dict) -> dict:
    """Create-or-update the bot user + generate API keys if needed — the
    same actions ensure_bot_user.py's own run_real() performs, inlined
    here rather than calling that function directly so this step reuses
    init_bot.py's own already-verified combined confirm-token instead of
    re-entering ensure_bot_user.py's separate token check (which expects
    its own narrower bot_user_plan_token, not this combined one)."""
    result = {"bot_email": bot_email, "user_created": False, "role_assigned": False,
              "re_enabled": False, "api_key": None, "api_secret": None}

    if plan["user_needed"]:
        _step(f"Create User: {bot_email}")
        erp_client.mutate_resource(
            tag, ensure_bot_user.USER_DOCTYPE, "create", payload={
                "email": bot_email, "first_name": "Qkeee Bot", "send_welcome_email": 0,
                "user_type": "System User", "enabled": 1, "roles": [{"role": ROLE_NAME}],
            },
            mode="read-write", requested_by=requested_by,
            user_approved=True, approval_note=approval_note,
        )
        print(f"Created User '{bot_email}' with role '{ROLE_NAME}'.")
        result["user_created"] = True
        result["role_assigned"] = True
    elif plan["user_role_needed"] or plan["enable_needed"]:
        _step(f"Update User: {bot_email}")
        user_doc = ensure_bot_user.get_bot_user(tag, bot_email)
        update = {}
        if plan["user_role_needed"]:
            update["roles"] = list(user_doc.get("roles", [])) + [{"role": ROLE_NAME}]
            result["role_assigned"] = True
        if plan["enable_needed"]:
            update["enabled"] = 1
            result["re_enabled"] = True
        erp_client.mutate_resource(
            tag, ensure_bot_user.USER_DOCTYPE, "update", payload=update, name=bot_email,
            mode="read-write", requested_by=requested_by,
            user_approved=True, approval_note=approval_note,
        )
        print(f"Updated User '{bot_email}'.")

    if plan["keys_needed"]:
        _step(f"Generate API keys for: {bot_email}")
        cfg = erp_client.get_env_config(tag)
        gen = erp_client._request(
            cfg, "POST", "/api/method/frappe.core.doctype.user.user.generate_keys",
            payload={"user": bot_email},
        )
        keys = gen.get("message") or {}
        result["api_key"] = keys.get("api_key")
        result["api_secret"] = keys.get("api_secret")
        if result["api_key"] and result["api_secret"]:
            print(f"Generated a fresh API key/secret for '{bot_email}'. THIS IS THE ONLY TIME "
                  f"the secret is shown — copy it now:")
            print(f"  QKEEE_ERP_{tag.upper()}_API_KEY={result['api_key']}")
            print(f"  QKEEE_ERP_{tag.upper()}_API_SECRET={result['api_secret']}")
            env_created = erp_client.ensure_qkeee_env_file_skeleton()
            print(f"{'Created' if env_created else 'Found existing'} "
                  f"{erp_client._qkeee_env_file_path()} — paste the two lines above into it "
                  f"yourself (plus QKEEE_ERP_{tag.upper()}_BASE_URL), never by having this "
                  f"tool read the file back to confirm.")
        else:
            print("WARNING: generate_keys call succeeded but returned no key/secret pair — "
                  "check the target's Frappe version supports this API method, or generate "
                  "manually via User -> API Access -> Generate Keys in the ERPNext UI.")
    return result


def run_dry_run(tag: str, requested_by: str, bot_email: str = None) -> dict:
    _step("Health check")
    print(json.dumps(erp_client.health_check(tag), indent=2))

    _step("Plan")
    plan = compute_plan(tag, bot_email)
    nothing_needed = (not plan["role_needed"] and not plan["doctypes_needed"]
                       and not plan["personas_needed"] and not plan["user_needed"]
                       and not plan["user_role_needed"] and not plan["enable_needed"]
                       and not plan["keys_needed"])
    if nothing_needed:
        print("Nothing to do — role, doctypes, personas"
              f"{', and bot user' if bot_email else ''} already in place.")
        return {"tag": tag, "dry_run": True, **plan, "confirm_token": None, "issued_at": None}

    issued_at = int(time.time())
    token = full_init_plan_token(
        tag, requested_by, plan["role_needed"], plan["doctypes_needed"], plan["personas_needed"],
        bot_email=plan["bot_email"], user_needed=plan["user_needed"],
        user_role_needed=plan["user_role_needed"], enable_needed=plan["enable_needed"],
        keys_needed=plan["keys_needed"], issued_at=issued_at,
    )

    if plan["role_needed"]:
        print(f"[dry-run] Would create Role '{ROLE_NAME}'.")
    for name in plan["doctypes_needed"]:
        doctype_def = next(d for d in ALL_DOCTYPES if d["name"] == name)
        print(f"[dry-run] Would create DocType '{name}' with "
              f"{len(doctype_def['fields'])} fields, "
              f"{len(doctype_def['permissions'])} permission rows.")
    for code in plan["personas_needed"]:
        print(f"[dry-run] Would register Persona '{code}'.")
    if bot_email:
        if plan["user_needed"]:
            print(f"[dry-run] Would create User '{bot_email}' (System User, no welcome email) "
                  f"with role '{ROLE_NAME}', then generate a fresh API key/secret for it.")
        else:
            if plan["user_role_needed"]:
                print(f"[dry-run] Would add role '{ROLE_NAME}' to '{bot_email}'.")
            if plan["enable_needed"]:
                print(f"[dry-run] Would re-enable '{bot_email}'.")
            if plan["keys_needed"]:
                print(f"[dry-run] Would generate a fresh API key/secret for '{bot_email}'.")

    _step("Confirm token")
    print(f"To run this for real, re-invoke with:\n"
          f"  --confirm-token {token} --issued-at {issued_at}\n"
          f"Valid for {DEFAULT_TOKEN_TTL_SECONDS // 60} minutes from now. "
          f"Only pass this back after the user has explicitly confirmed "
          f"the plan printed above.")
    return {"tag": tag, "dry_run": True, **plan, "confirm_token": token, "issued_at": issued_at}


def run_real(tag: str, requested_by: str, confirm_token: str, issued_at: int,
             bot_email: str = None) -> dict:
    _step("Health check")
    print(json.dumps(erp_client.health_check(tag), indent=2))

    _step("Plan (recomputed against current target state)")
    plan = compute_plan(tag, bot_email)

    action_needed = (plan["role_needed"] or plan["doctypes_needed"] or plan["personas_needed"]
                      or plan["user_needed"] or plan["user_role_needed"]
                      or plan["enable_needed"] or plan["keys_needed"])
    if action_needed:
        if not confirm_token or issued_at is None:
            raise erp_client.ConnectorError(
                "This run would create/change records but no --confirm-token/--issued-at was "
                "given. Run with --dry-run first, show the plan to the user, and only "
                "re-invoke with the token it prints after they explicitly confirm."
            )
        if not is_fresh(issued_at):
            raise erp_client.ConnectorError(
                f"--issued-at is stale or invalid (must be within "
                f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes). Run --dry-run again to get "
                f"a fresh token."
            )
        expected = full_init_plan_token(
            tag, requested_by, plan["role_needed"], plan["doctypes_needed"], plan["personas_needed"],
            bot_email=plan["bot_email"], user_needed=plan["user_needed"],
            user_role_needed=plan["user_role_needed"], enable_needed=plan["enable_needed"],
            keys_needed=plan["keys_needed"], issued_at=issued_at,
        )
        if expected != confirm_token:
            raise erp_client.ConnectorError(
                "--confirm-token does not match the current plan for this target. Either "
                "the target's state changed since the dry-run (something was created/"
                "removed out of band), or requested_by/tag/bot_email differs from the "
                "dry-run. Run --dry-run again and re-confirm with the new token."
            )

    approval_note = f"dry-run confirmed by {requested_by}, token {confirm_token[:8]}..." if confirm_token else \
        "no records needed creating — nothing to confirm"

    _step(f"Role: {ROLE_NAME}")
    role_created = ensure_role(tag, requested_by, approval_note)

    results = {}
    for doctype_def in ALL_DOCTYPES:
        _step(f"DocType: {doctype_def['name']}")
        results[doctype_def["name"]] = ensure_doctype(tag, doctype_def, requested_by, approval_note)

    _step("Personas")
    persona_results = ensure_personas(tag, requested_by)

    _step("qkeee-erp.env")
    env_created = erp_client.ensure_qkeee_env_file_skeleton()
    print(f"{'Created empty' if env_created else 'Found existing'} "
          f"{erp_client._qkeee_env_file_path()}.")

    bot_user_result = None
    if bot_email:
        bot_user_result = ensure_bot_user_step(tag, bot_email, requested_by, approval_note, plan)

    _step("Summary")
    summary = {
        "tag": tag,
        "dry_run": False,
        "role_created": role_created,
        "doctypes_created": [name for name, created in results.items() if created],
        "doctypes_already_present": [name for name, created in results.items() if not created],
        "personas": persona_results,
        "qkeee_env_file_created": env_created,
        "bot_user": {k: v for k, v in bot_user_result.items() if k not in ("api_key", "api_secret")}
        if bot_user_result else None,
    }
    print(json.dumps(summary, indent=2))
    return summary


def _cli():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tag", required=True, help="environment tag, from qkeee_erp.active_env")
    p.add_argument("--requested-by", required=True,
                   help="ERPNext user id/email running this init (elevated/admin account)")
    p.add_argument("--bot-email",
                   help="optional: also provision this dedicated bot/service-account User "
                        "(create-or-update, role, enable, API keys) in the same dry-run/real-run "
                        "as the role/doctypes/personas. Omit to skip bot-user provisioning "
                        "entirely (run ensure_bot_user.py separately instead).")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be created, and print a confirm-token, without writing anything")
    p.add_argument("--confirm-token", help="token printed by a prior --dry-run")
    p.add_argument("--issued-at", type=int, help="issued_at epoch seconds printed by a prior --dry-run")
    args = p.parse_args()

    try:
        if args.dry_run:
            run_dry_run(args.tag, args.requested_by, bot_email=args.bot_email)
        else:
            run_real(args.tag, args.requested_by, args.confirm_token, args.issued_at,
                      bot_email=args.bot_email)
    except erp_client.ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
