#!/usr/bin/env python3
"""
qkeee-erp-system-admin — confirmation-token helper.

This persona has the widest blast radius in the qkeee-erp library (per
the module plan's design note): permission/role changes and destructive
actions (disable/delete a user, delete a customization) must be scoped
explicitly and confirmed — never broad or implicit. Both categories get
a DOUBLE confirm (state the exact change, then ask again), same pattern
as qkeee-erp-fixed-asset-manager's depreciation-run/disposal gate.
Creating a user with an elevated role (System Manager/Administrator) and
the two moderate-but-real-risk config writes (Webhook create, Workflow
is_active toggle) get the same code-level token backstop, added after
this skill's adversarial review found them under-gated relative to their
actual blast radius.

The render scripts (render_permission_change.py,
render_destructive_action.py, render_user_draft.py,
render_config_change.py) compute a token from the exact facts just shown
to the user, INCLUDING the render timestamp (`issued_at`). erp_client.py's
gated call paths recompute the same token from the actual call's own
arguments and refuse to proceed without a match — a caller cannot skip
straight from render to execute without round-tripping the rendered
facts back in.

Important limitation, stated plainly rather than implied: a matching
token proves the call's facts are IDENTICAL to what was rendered and
that the render happened recently (see is_fresh()). It does NOT prove a
human actually read and approved the render — nothing in this file (or
in erp_client.py) can observe that. The calling skill's own discipline
(never compute a token and immediately consume it in the same turn;
only use a token after the user's own reply in the transcript
affirmatively confirms the specific rendered action) is what makes the
"double confirm" real. Treat the token as a tamper/staleness check, not
a human-presence proof.
"""

import hashlib
import json
import time

# How long a rendered confirmation stays valid. Chosen to be long enough
# for a human to read a confirmation and reply, short enough that a
# token from a stale/abandoned conversation can't be replayed days
# later against current (possibly different) live data.
DEFAULT_TOKEN_TTL_SECONDS = 900  # 15 minutes

# Tolerance for issued_at appearing slightly in the future, to absorb
# clock skew between the process that rendered and the process that
# executes — not a loophole for backdating, just slack for real clocks.
CLOCK_SKEW_TOLERANCE_SECONDS = 30


def compute_token(**fields) -> str:
    """Deterministic short token over the given facts."""
    canonical = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def is_fresh(issued_at: int, max_age_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
             now: int = None) -> bool:
    """True if issued_at is within [now - max_age_seconds, now + skew-tolerance].

    Rejects both stale tokens (replay of an old confirmation) and
    implausibly-future ones (clock manipulation / fabricated issued_at).
    """
    now = int(now) if now is not None else int(time.time())
    age = now - int(issued_at)
    return -CLOCK_SKEW_TOLERANCE_SECONDS <= age <= max_age_seconds


def permission_change_token(action: str, doctype: str, role: str, permlevel: int,
                             ptype: str = "", value=None, issued_at: int = None) -> str:
    """action: 'add' | 'update' | 'remove' | 'reset'.

    issued_at (epoch seconds) is required — it's the render timestamp,
    baked into the token so a token can't outlive DEFAULT_TOKEN_TTL_SECONDS
    (checked separately via is_fresh(), not by this function).
    """
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return compute_token(
        kind="permission_change",
        action=action,
        doctype=doctype,
        role=role,
        permlevel=int(permlevel),
        ptype=ptype,
        value=value,
        issued_at=int(issued_at),
    )


def destructive_action_token(action: str, target_doctype: str, target_name: str,
                              reason: str, issued_at: int = None) -> str:
    """action e.g. 'disable_user' | 'delete_user' | 'delete_custom_field' |
    'delete_property_setter' | 'delete_webhook' | 'delete_workflow'. reason
    is part of the token so a caller can't reuse an old token against a
    new/different stated reason. issued_at required, see permission_change_token."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return compute_token(
        kind="destructive_action",
        action=action,
        target_doctype=target_doctype,
        target_name=target_name,
        reason=reason,
        issued_at=int(issued_at),
    )


def elevated_user_token(email: str, roles: list, issued_at: int = None) -> str:
    """Gates creating a user with an elevated role (System Manager /
    Administrator) — added because this was previously the one
    highest-privilege action in the skill with no code-level backstop
    (only a boolean elevated_roles_acknowledged flag)."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return compute_token(
        kind="elevated_user",
        email=email,
        roles=sorted(roles),
        issued_at=int(issued_at),
    )


def config_change_token(kind: str, doctype: str, identifier: str, reason: str,
                         issued_at: int = None) -> str:
    """Gates the two moderate-but-real-risk config writes that previously
    had only a prompt-level single confirm: kind='create_webhook' (an
    outbound data destination — SSRF/exfiltration surface) and
    kind='toggle_workflow' (can halt every in-flight approval on that
    document type). identifier is the webhook request_url or the
    workflow's document_type — whatever the render step displayed."""
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return compute_token(
        kind="config_change",
        change_kind=kind,
        doctype=doctype,
        identifier=identifier,
        reason=reason,
        issued_at=int(issued_at),
    )
