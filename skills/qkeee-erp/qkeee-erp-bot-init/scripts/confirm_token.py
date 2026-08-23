#!/usr/bin/env python3
"""
qkeee-erp-bot-init — confirmation-token helper.

This skill creates DocType/Role records at System-Manager level against a
live ERPNext instance — a one-time/occasional structural change, but one
with a wider blast radius than most persona-skill writes (schema-level,
instance-wide, touches every qkeee-erp-* skill that later relies on the
audit trail). SKILL.md's dry-run-then-real-run instruction is a prompt-
level confirm; this module adds the same code-level backstop
qkeee-erp-system-admin uses for its comparably-risky actions
(render_*.py + confirm_token.py) — a caller cannot skip straight from
"never ran a dry-run" to a real run without round-tripping the exact plan
the dry-run showed the user.

The token is computed over the *plan* (which doctypes/role would actually
be created, given what already exists on the target right now), not just
the CLI arguments — so if the target's state changes between dry-run and
real-run (someone else already created one of the doctypes, or a doctype
was removed), the plan changes, the token no longer matches, and init_bot
refuses to proceed silently on stale facts.

Same limitation as system-admin's confirm_token.py, stated plainly: a
matching token proves the real-run's plan is IDENTICAL to what the
dry-run showed and that the dry-run happened recently (see is_fresh()).
It does NOT prove a human actually read and approved it — that's the
calling skill's own discipline (only pass --confirm-token after the
user's own reply in the transcript affirmatively confirms the dry-run
output), same as documented in system-admin's version of this file.
"""

import hashlib
import json
import time

# How long a dry-run's token stays valid. Long enough for a human to read
# the dry-run output and reply, short enough that a token from a stale/
# abandoned conversation can't be replayed days later against a target
# whose schema may have moved on.
DEFAULT_TOKEN_TTL_SECONDS = 900  # 15 minutes

# Tolerance for issued_at appearing slightly in the future, to absorb
# clock skew between the process that dry-ran and the process that
# executes for real — not a loophole for backdating, just slack for real
# clocks.
CLOCK_SKEW_TOLERANCE_SECONDS = 30


def compute_token(**fields) -> str:
    """Deterministic short token over the given facts."""
    canonical = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def is_fresh(issued_at: int, max_age_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
             now: int = None) -> bool:
    """True if issued_at is within [now - max_age_seconds, now + skew-tolerance].

    Rejects both stale tokens (replay of an old render/confirm) and
    implausibly-future ones (clock manipulation / fabricated issued_at).
    """
    now = int(now) if now is not None else int(time.time())
    age = now - int(issued_at)
    return -CLOCK_SKEW_TOLERANCE_SECONDS <= age <= max_age_seconds


def init_plan_token(tag: str, requested_by: str, role_needed: bool,
                     doctypes_needed: list, issued_at: int = None) -> str:
    """Token over the init plan: which tag, who's running it, whether the
    Role needs creating, and exactly which doctype names need creating
    (sorted, so ordering never causes a spurious mismatch). issued_at
    (epoch seconds) is required — it's the dry-run timestamp, baked into
    the token so it can't outlive DEFAULT_TOKEN_TTL_SECONDS (checked
    separately via is_fresh(), not by this function)."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the dry-run-time epoch seconds.")
    return compute_token(
        kind="bot_init_plan",
        tag=tag,
        requested_by=requested_by,
        role_needed=bool(role_needed),
        doctypes_needed=sorted(doctypes_needed),
        issued_at=int(issued_at),
    )


def full_init_plan_token(tag: str, requested_by: str, role_needed: bool, doctypes_needed: list,
                          personas_needed: list, bot_email: str = None, user_needed: bool = False,
                          user_role_needed: bool = False, enable_needed: bool = False,
                          keys_needed: bool = False, issued_at: int = None) -> str:
    """Token over init_bot.py's FULL combined plan — role, doctypes,
    persona registrations, and (if --bot-email was given) the bot-user
    plan — all confirmed in one dry-run/real-run round trip rather than
    four separate ones. Same determinism/freshness contract as
    init_plan_token()/bot_user_plan_token() above; this supersedes both
    for init_bot.py's own CLI (which now always covers role+doctypes+
    personas, and optionally the bot user), while those two functions
    stay in place for ensure_bot_user.py's standalone/direct-invocation
    path, which still round-trips its own narrower token."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the dry-run-time epoch seconds.")
    return compute_token(
        kind="bot_init_full_plan",
        tag=tag,
        requested_by=requested_by,
        role_needed=bool(role_needed),
        doctypes_needed=sorted(doctypes_needed),
        personas_needed=sorted(personas_needed),
        bot_email=bot_email or "",
        user_needed=bool(user_needed),
        user_role_needed=bool(user_role_needed),
        enable_needed=bool(enable_needed),
        keys_needed=bool(keys_needed),
        issued_at=int(issued_at),
    )


def bot_user_plan_token(tag: str, requested_by: str, bot_email: str, user_needed: bool,
                         role_needed: bool, enable_needed: bool, keys_needed: bool,
                         issued_at: int = None) -> str:
    """Same pattern as init_plan_token(), for ensure_bot_user.py's plan:
    which tag/requester/target bot_email, and which of user-create /
    role-assign / re-enable / key-generation steps the current live state
    actually needs. Re-derived from the live target on every dry-run AND
    real-run, so a real run always checks its token against current state
    — not a plan that may have gone stale (e.g. someone else already
    created the user, or already generated keys, between dry-run and
    real-run)."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the dry-run-time epoch seconds.")
    return compute_token(
        kind="bot_user_plan",
        tag=tag,
        requested_by=requested_by,
        bot_email=bot_email,
        user_needed=bool(user_needed),
        role_needed=bool(role_needed),
        enable_needed=bool(enable_needed),
        keys_needed=bool(keys_needed),
        issued_at=int(issued_at),
    )
