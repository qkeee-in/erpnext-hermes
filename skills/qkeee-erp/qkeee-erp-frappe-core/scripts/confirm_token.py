#!/usr/bin/env python3
"""
qkeee-erp-frappe-core — shared confirmation-token primitives, plus this
skill's own advisory-first write-gate token constructor.

Several persona skills gate an irreversible or high-blast-radius write
(depreciation runs, asset disposal, destructive sysadmin actions, bot-user
provisioning) behind a DOUBLE confirm: the render/stage step computes a
token from the exact facts just shown to the user, and the execute step
recomputes the same token from the RPC call's own arguments, refusing to
proceed unless they match. This closes the gap where nothing ties a
render step to its execute step — without it, a caller could render a
confirmation and immediately fire the write in the same turn without the
user having actually seen the rendered facts.

This module owns the two primitives every such token needs:
  - compute_token(**fields)  — deterministic hash over arbitrary facts.
  - is_fresh(issued_at, ...) — reject stale (replayed) or implausibly-
    future tokens.

...plus this skill's OWN token constructor, `advisory_write_token()` (see
below) — merged in from the former qkeee-erp-catch-all skill, 2026-08-18.
Unlike every other persona skill, which gates only its highest-risk
actions, this skill's `gated_mutate_resource()` (erp_client.py) runs
EVERY create/update/submit/cancel/delete through this one constructor,
unconditionally — nothing this skill investigates has had the design-
time capability review that lets the eight named persona skills be more
assertive.

Other capability-specific token constructors (e.g. depreciation_run_token(),
permission_change_token(), destructive_action_token()) do NOT live here —
those stay in the owning persona skill's own confirm_token.py, built on
top of the two shared primitives, same as erp_client.py's split between
generic connector primitives (this skill) and persona-specific business
logic (the persona skill). A persona skill's confirm_token.py should:

    from confirm_token import compute_token, is_fresh, DEFAULT_TOKEN_TTL_SECONDS

    def my_action_token(asset: str, amount: float, issued_at: int = None) -> str:
        return compute_token(
            kind="my_action",
            asset=asset,
            amount=round(float(amount), 2),
            issued_at=issued_at or int(time.time()),
        )

Every token constructor MUST include an `issued_at` timestamp field and
the execute step MUST check it with is_fresh() before honoring the token
— a token with no freshness check never expires, which defeats the
anti-replay property this whole mechanism exists for.
"""

import hashlib
import json
import time

# 15 minutes: long enough to cover a realistic render-then-confirm human
# turnaround, short enough that a token can't be usefully replayed against
# facts that have since changed (e.g. a revalued asset, an amended draft).
DEFAULT_TOKEN_TTL_SECONDS = 900

# Small tolerance for clock skew between the process that issued the token
# and the process that later validates it — not a security boundary, just
# enough to avoid rejecting a legitimately-fresh token over a few seconds
# of drift.
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


def advisory_write_token(action: str, doctype: str, name: str, payload: dict,
                          requested_by: str, issued_at: int = None) -> str:
    """Gates every create/update/submit/cancel/delete this skill performs
    — see module docstring for why this is unconditional here, unlike the
    narrower gating in system-admin/fixed-asset-manager's copies.

    payload is folded into the token as-is (sorted-key JSON) so the token
    is bound to the exact drafted field values, not just the doctype/
    action/name shape — a caller can't render one payload and execute a
    different one under the same token.
    """
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return compute_token(
        kind="advisory_write",
        action=action,
        doctype=doctype,
        name=name or "",
        payload=payload or {},
        requested_by=requested_by or "",
        issued_at=int(issued_at),
    )
