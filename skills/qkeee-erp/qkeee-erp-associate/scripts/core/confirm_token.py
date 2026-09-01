#!/usr/bin/env python3
"""
qkeee-erp-associate core — shared confirmation-token primitives, plus this
skill's advisory-first write-gate token constructor.

Several domains gate an irreversible or high-blast-radius write
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
below). Unlike a domain module's own capability-reviewed writes, this
skill's `gated_mutate_resource()` (client.py) runs EVERY create/update/
submit/cancel/delete that falls outside a named domain's allowlist
through this one constructor, unconditionally — nothing outside a
domain's own ALLOWED_WRITE_DOCTYPES has had that design-time capability
review.

Other capability-specific token constructors (e.g. depreciation_run_token(),
permission_change_token(), destructive_action_token()) do NOT live here —
each domain module that needs one carries its own copy (see
domains/fixed_assets.py, domains/system_admin.py), built on top of the two
shared primitives:

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
    """Gates every create/update/submit/cancel/delete that falls outside a
    named domain's allowlist — see module docstring for why this is
    unconditional here, unlike a domain module's own narrower gating.

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
