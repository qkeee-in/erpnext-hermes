#!/usr/bin/env python3
"""
qkeee-erp-bot-init — detect/provision the steady-state qkeee-erp-bot
service-account User (as distinct from ensure the Qkeee Bot Persona/
Session/Message/Audit Log doctypes + Role, which init_bot.py handles).

Persona skills authenticate to ERPNext as ONE shared bot/service account
per environment tag (QKEEE_ERP_<TAG>_API_KEY/_SECRET — see
qkeee-erp-frappe-core/SKILL.md's "Bot account — mandatory" section). That
account must be a dedicated ERPNext User, never a real staff member's
personal login, and should hold the `Qkeee Bot` role once it exists (see
doctype_defs.py / init_bot.py) so the audit-trail doctypes' permission
matrix (Qkeee Bot role: read/write/create on Session/Message/Audit Log)
actually applies to it. This script closes the gap where nothing in the
library previously checked any of that automatically — an org could run
persona skills for months against a personal login and nobody would
notice.

This script does NOT create the Qkeee Bot role or the audit doctypes
themselves — it requires the Role to already exist (run init_bot.py
first if not) and errors out with that instruction if it's missing,
rather than silently creating a User with a role that doesn't exist yet.

Same elevated-credential requirement as init_bot.py: run this against
the target tag's ELEVATED (System Manager/Administrator) API key, never
the shared qkeee-erp-bot@<org> key itself — creating/editing a User and
generating API keys both require System-Manager-level permission.

Same dry-run -> confirm-token -> real-run discipline as init_bot.py (see
confirm_token.py's bot_user_plan_token()): a real run must pass back the
exact token a prior dry-run printed, recomputed against the target's
*current* live state, so it can't proceed on stale facts or without a
human having seen the plan first.

Newly-generated API key/secret are printed to stdout ONCE, for the
operator to copy into QKEEE_ERP_<TAG>_API_KEY / _API_SECRET themselves —
this script never writes them to a file, logs them to the Qkeee Bot audit
trail, or stores them anywhere else. There is no way to recover a printed
secret after the terminal scrolls past it; re-run with --regenerate-keys
if it's lost (this invalidates the previous key).

Usage:
    python ensure_bot_user.py --tag qa --bot-email qkeee-erp-bot@org.com \\
        --requested-by admin@org.com --dry-run
    python ensure_bot_user.py --tag qa --bot-email qkeee-erp-bot@org.com \\
        --requested-by admin@org.com \\
        --confirm-token <token> --issued-at <epoch>
"""

import argparse
import json
import sys
import time

import erp_client
from confirm_token import bot_user_plan_token, is_fresh, DEFAULT_TOKEN_TTL_SECONDS
from doctype_defs import ROLE_NAME

USER_DOCTYPE = "User"


def _step(label):
    print(f"--- {label} ---")


def get_bot_user(tag: str, bot_email: str) -> dict:
    """Full User doc if it exists, else None. Uses strip_noise=False —
    the `roles` child table and `api_key` field matter here and would
    otherwise be at risk from a future _NOISE_FIELDS change."""
    if not erp_client.resource_exists(tag, USER_DOCTYPE, bot_email):
        return None
    return erp_client.get_resource(tag, USER_DOCTYPE, bot_email, strip_noise=False).get("data")


def has_role(user_doc: dict, role_name: str) -> bool:
    return any(r.get("role") == role_name for r in (user_doc or {}).get("roles", []))


def compute_plan(tag: str, bot_email: str) -> dict:
    """Existence/role/enabled/key-checks against the live target right
    now. Re-run fresh on every dry-run AND every real run — see module
    docstring."""
    if not erp_client.resource_exists(tag, "Role", ROLE_NAME):
        raise erp_client.ConnectorError(
            f"Role '{ROLE_NAME}' does not exist on tag '{tag}' yet. Run "
            f"init_bot.py against this tag first (it provisions the role "
            f"and the audit-trail doctypes) — this script assigns that "
            f"role to the bot user, it doesn't create it."
        )

    user_doc = get_bot_user(tag, bot_email)
    if user_doc is None:
        return {
            "bot_email": bot_email, "user_exists": False,
            "user_needed": True, "role_needed": True,
            "enable_needed": False, "keys_needed": True,
        }

    return {
        "bot_email": bot_email, "user_exists": True,
        "user_needed": False,
        "role_needed": not has_role(user_doc, ROLE_NAME),
        "enable_needed": not user_doc.get("enabled", 1),
        "keys_needed": not user_doc.get("api_key"),
    }


def run_dry_run(tag: str, bot_email: str, requested_by: str) -> dict:
    _step("Health check")
    print(json.dumps(erp_client.health_check(tag), indent=2))

    _step("Plan")
    plan = compute_plan(tag, bot_email)
    nothing_to_do = not any(plan[k] for k in ("user_needed", "role_needed", "enable_needed", "keys_needed"))
    if nothing_to_do:
        print(f"Nothing to do — '{bot_email}' already exists, holds '{ROLE_NAME}', "
              f"is enabled, and already has an API key configured.")
        return {"tag": tag, "dry_run": True, **plan, "confirm_token": None, "issued_at": None}

    issued_at = int(time.time())
    token = bot_user_plan_token(tag, requested_by, bot_email, plan["user_needed"],
                                 plan["role_needed"], plan["enable_needed"], plan["keys_needed"],
                                 issued_at)

    if plan["user_needed"]:
        print(f"[dry-run] Would create User '{bot_email}' (System User, no welcome email) "
              f"with role '{ROLE_NAME}', then generate a fresh API key/secret for it.")
    else:
        print(f"[dry-run] User '{bot_email}' already exists.")
        if plan["role_needed"]:
            print(f"[dry-run] Would add role '{ROLE_NAME}' to '{bot_email}' "
                  f"(currently missing it).")
        if plan["enable_needed"]:
            print(f"[dry-run] Would re-enable '{bot_email}' (currently disabled).")
        if plan["keys_needed"]:
            print(f"[dry-run] Would generate a fresh API key/secret for '{bot_email}' "
                  f"(no key currently configured).")

    _step("Confirm token")
    print(f"To run this for real, re-invoke with:\n"
          f"  --confirm-token {token} --issued-at {issued_at}\n"
          f"Valid for {DEFAULT_TOKEN_TTL_SECONDS // 60} minutes from now. "
          f"Only pass this back after the user has explicitly confirmed the plan "
          f"printed above — including that '{bot_email}' is meant to be a dedicated "
          f"bot account, not someone's personal login.")
    return {"tag": tag, "dry_run": True, **plan, "confirm_token": token, "issued_at": issued_at}


def run_real(tag: str, bot_email: str, requested_by: str, confirm_token: str, issued_at: int,
             regenerate_keys: bool = False) -> dict:
    _step("Health check")
    print(json.dumps(erp_client.health_check(tag), indent=2))

    _step("Plan (recomputed against current target state)")
    plan = compute_plan(tag, bot_email)
    if regenerate_keys:
        plan["keys_needed"] = True

    action_needed = any(plan[k] for k in ("user_needed", "role_needed", "enable_needed", "keys_needed"))
    if action_needed:
        if not confirm_token or issued_at is None:
            raise erp_client.ConnectorError(
                "This run would create/change the bot user but no --confirm-token/--issued-at "
                "was given. Run with --dry-run first, show the plan to the user, and only "
                "re-invoke with the token it prints after they explicitly confirm."
            )
        if not is_fresh(issued_at):
            raise erp_client.ConnectorError(
                f"--issued-at is stale or invalid (must be within "
                f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes). Run --dry-run again for a fresh token."
            )
        expected = bot_user_plan_token(tag, requested_by, bot_email, plan["user_needed"],
                                        plan["role_needed"], plan["enable_needed"], plan["keys_needed"],
                                        issued_at)
        if expected != confirm_token:
            raise erp_client.ConnectorError(
                "--confirm-token does not match the current plan for this target. Either the "
                "target's state changed since the dry-run, or requested_by/tag/bot_email differs "
                "from the dry-run, or --regenerate-keys was added on this run but not the dry-run. "
                "Run --dry-run again and re-confirm with the new token."
            )

    approval_note = f"dry-run confirmed by {requested_by}, token {confirm_token[:8]}..." if confirm_token else \
        "no changes needed — nothing to confirm"

    result = {"tag": tag, "dry_run": False, "bot_email": bot_email,
              "user_created": False, "role_assigned": False, "re_enabled": False,
              "api_key": None, "api_secret": None}

    if plan["user_needed"]:
        _step(f"Create User: {bot_email}")
        erp_client.mutate_resource(
            tag, USER_DOCTYPE, "create", payload={
                "email": bot_email,
                "first_name": "Qkeee Bot",
                "send_welcome_email": 0,
                "user_type": "System User",
                "enabled": 1,
                "roles": [{"role": ROLE_NAME}],
            },
            mode="read-write", requested_by=requested_by,
            user_approved=True, approval_note=approval_note,
        )
        print(f"Created User '{bot_email}' with role '{ROLE_NAME}'.")
        result["user_created"] = True
        result["role_assigned"] = True
    else:
        if plan["role_needed"] or plan["enable_needed"]:
            _step(f"Update User: {bot_email}")
            user_doc = get_bot_user(tag, bot_email)
            update = {}
            if plan["role_needed"]:
                roles = list(user_doc.get("roles", [])) + [{"role": ROLE_NAME}]
                update["roles"] = roles
                result["role_assigned"] = True
            if plan["enable_needed"]:
                update["enabled"] = 1
                result["re_enabled"] = True
            erp_client.mutate_resource(
                tag, USER_DOCTYPE, "update", payload=update, name=bot_email,
                mode="read-write", requested_by=requested_by,
                user_approved=True, approval_note=approval_note,
            )
            print(f"Updated User '{bot_email}': "
                  f"{'added role ' + ROLE_NAME + '; ' if plan['role_needed'] else ''}"
                  f"{'re-enabled' if plan['enable_needed'] else ''}".strip("; ") or "no changes")

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
            print("Set these in the operator's shell profile / OS credential manager for the "
                  "persona skills to use — never in agent-curated memory or a committed file.")
        else:
            print("WARNING: generate_keys call succeeded but returned no key/secret pair — "
                  "check the target's Frappe version supports this API method, or generate "
                  "manually via User -> API Access -> Generate Keys in the ERPNext UI.")

    _step("Summary")
    summary = {k: v for k, v in result.items() if k not in ("api_key", "api_secret")}
    summary["keys_generated"] = bool(result["api_key"])
    print(json.dumps(summary, indent=2))
    return result


def _cli():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tag", required=True, help="environment tag, from qkeee_erp.active_env")
    p.add_argument("--bot-email", required=True,
                   help="dedicated bot/service-account User id, e.g. qkeee-erp-bot@org.com")
    p.add_argument("--requested-by", required=True,
                   help="ERPNext user id/email running this (elevated/admin account)")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would change, and print a confirm-token, without writing anything")
    p.add_argument("--confirm-token", help="token printed by a prior --dry-run")
    p.add_argument("--issued-at", type=int, help="issued_at epoch seconds printed by a prior --dry-run")
    p.add_argument("--regenerate-keys", action="store_true",
                   help="force a fresh API key/secret even if one is already configured "
                        "(invalidates the old one) — must still go through dry-run/confirm")
    args = p.parse_args()

    try:
        if args.dry_run:
            run_dry_run(args.tag, args.bot_email, args.requested_by)
        else:
            run_real(args.tag, args.bot_email, args.requested_by, args.confirm_token,
                      args.issued_at, regenerate_keys=args.regenerate_keys)
    except erp_client.ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
