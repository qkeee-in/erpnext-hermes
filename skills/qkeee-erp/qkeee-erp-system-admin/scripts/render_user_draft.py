#!/usr/bin/env python3
"""
qkeee-erp-system-admin — user creation & role assignment draft renderer.

The non-negotiable: a role grant must be scoped explicitly — never
broad or implicit. "Give them admin access" must resolve to specific
role names before this renderer will mark a draft ready; it never
infers roles from a vague request itself (that's the calling skill's
job, in conversation with the user, before this script is invoked).

Elevated roles (System Manager, Administrator) get a loud, separate
warning and require `elevated_roles_acknowledged: true` before the
draft is marked ready — creating a System Manager is the single
easiest way to accidentally hand out full admin rights, and this
skill's design note calls for scrutinizing exactly that hardest.

This script NEVER calls ERPNext. It only formats the confirmation.
"""

import json
import sys
import time

from confirm_token import DEFAULT_TOKEN_TTL_SECONDS, elevated_user_token

ELEVATED_ROLES = {"System Manager", "Administrator"}


class RenderError(Exception):
    pass


def render_user_draft(email: str, first_name: str, roles: list, existing_roles: list = None,
                       send_welcome_email: bool = False, elevated_roles_acknowledged: bool = False,
                       notes: str = "", issued_at: int = None) -> str:
    """
    roles: list of exact role names to grant — refuses "ready" if empty
      (a user with zero roles can log in but do nothing useful; if that
      really is the intent, the caller passes roles=["All"] or similar
      explicitly, not an empty list by omission).
    existing_roles: pass the role names known to exist on this instance
      (from erp_client.query_resource(tag, "Role", ...)) so a typo'd
      role name is caught here, not as an opaque ERPNext error after
      create.
    elevated_roles_acknowledged: must be explicitly true if `roles`
      contains "System Manager" or "Administrator" — refuses "ready"
      otherwise, regardless of how many other roles are also requested.
    """
    if not email or "@" not in email:
        raise RenderError(f"email must be a valid-looking email address, got {email!r}.")
    if not first_name:
        raise RenderError("first_name is required.")
    if not roles:
        raise RenderError(
            "roles must be a non-empty, explicit list of role names — never inferred from "
            "a vague request like 'give them access'. Resolve to specific roles first."
        )

    unknown = []
    if existing_roles is not None:
        unknown = [r for r in roles if r not in existing_roles]

    elevated = sorted(set(roles) & ELEVATED_ROLES)
    ready = not unknown and (not elevated or elevated_roles_acknowledged)

    lines = [
        f"# New user draft — `{email}`",
        "",
        f"**First name:** {first_name}  |  **Send welcome email:** {send_welcome_email}",
        f"**Roles requested:** {', '.join(roles)}",
        "",
    ]

    if unknown:
        lines.append(f"**BLOCKED — unknown role(s), not found on this instance: "
                      f"{', '.join(unknown)}.** Confirm exact role names (case-sensitive) "
                      "before proceeding — a typo'd role name would otherwise surface as an "
                      "opaque ERPNext error at create time instead of here.")
        lines.append("")

    token = None
    issued_at_val = None
    if elevated:
        lines.append(f"**ELEVATED ROLE(S) REQUESTED: {', '.join(elevated)}.** This grants "
                      "broad administrative rights across the whole instance, not scoped to "
                      "one module — confirm this is actually intended, not a shortcut for "
                      "narrower access the user actually needs.")
        if not elevated_roles_acknowledged:
            lines.append("**BLOCKED — elevated_roles_acknowledged is not set.** Explicit "
                          "acknowledgment is required before this draft can be marked ready.")
        issued_at_val = int(issued_at) if issued_at is not None else int(time.time())
        token = elevated_user_token(email, roles, issued_at_val)
        lines.append(
            f"**Elevated-role confirmation token:** `{token}`  |  **Issued at:** "
            f"{issued_at_val} (epoch seconds) — this is the single highest-privilege action "
            "this skill can take, so unlike a plain role grant it DOES get a code-level "
            "backstop: pass both token and issued_at to "
            "`erp_client.create_user(..., elevated_confirmation_token=..., issued_at=...)`. "
            "The call is refused without a fresh (within "
            f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes), matching token — re-render this "
            "draft if too much time has passed."
        )
        lines.append("")

    lines.append(f"**Ready to create:** {'YES' if ready else 'NO — see BLOCKED reason(s) above'}")
    lines.append("")
    if elevated:
        lines.append("**What happens on create:** a User record is created with "
                      f"`send_welcome_email={int(send_welcome_email)}` and the roles above "
                      "attached via the `roles` child table in the same call, via "
                      "`erp_client.create_user()` — which enforces the elevated-role token "
                      "gate above before it will call ERPNext.")
    else:
        lines.append("**What happens on create:** a User record is created with "
                      f"`send_welcome_email={int(send_welcome_email)}` and the roles above attached "
                      "via the `roles` child table in the same create call, via "
                      "`erp_client.create_user()`. No confirmation-token gate applies for "
                      "non-elevated role grants — get explicit user go-ahead on this draft, "
                      "then call `create_user(...)`.")

    if notes:
        lines += ["", "## Notes", "", notes]

    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_user_draft.py <path-to-json-input>", file=sys.stderr)
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
        out = render_user_draft(
            email=payload["email"],
            first_name=payload["first_name"],
            roles=payload.get("roles", []),
            existing_roles=payload.get("existing_roles"),
            send_welcome_email=payload.get("send_welcome_email", False),
            elevated_roles_acknowledged=payload.get("elevated_roles_acknowledged", False),
            notes=payload.get("notes", ""),
            issued_at=payload.get("issued_at"),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(out)


if __name__ == "__main__":
    _cli()
