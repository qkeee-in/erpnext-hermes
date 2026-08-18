#!/usr/bin/env python3
"""
qkeee-erp-fixed-asset-manager — confirmation-token helper.

Depreciation runs and disposals require a DOUBLE confirm per this
skill's non-negotiable. Previously that was enforced only in prose
(SKILL.md step 6: "ask again after showing it") with no code artifact
tying the render step to the execute step — nothing stopped a caller
from rendering the confirmation and immediately firing the RPC call in
the same turn.

This module closes that gap with a minimal mechanism: the render
scripts compute a token from the exact financial facts just shown to
the user (asset, schedule/method identity, and the total amount at
stake). erp_client.call_whitelisted_method() recomputes the same token
from the RPC call's own arguments and refuses to proceed unless the
caller supplies a matching token — i.e. the caller must have actually
run the render step against the same numbers before the write can
happen. This does not replace the human "ask again" step; it makes it
impossible to skip straight from render to execute without at least
round-tripping the rendered facts back in.

**Freshness retrofit (2026-08-18, synced from qkeee-erp-core):** this
file previously had no is_fresh() / issued_at — a computed token never
expired, so a stale render (against numbers that have since changed,
e.g. a revalued asset or an amended schedule) could still validate at
execute time. Both token constructors now take a mandatory `issued_at`
and erp_client.call_whitelisted_method() checks is_fresh() on it before
honoring a confirmation_token, same as every other qkeee-erp-* skill
that uses this double-confirm pattern.
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


def depreciation_run_token(asset: str, asset_depr_schedule: str, as_of_date: str,
                            total_depreciation: float, issued_at: int) -> str:
    return compute_token(
        kind="depreciation_run",
        asset=asset,
        asset_depr_schedule=asset_depr_schedule,
        as_of_date=as_of_date,
        total_depreciation=round(float(total_depreciation), 2),
        issued_at=int(issued_at),
    )


def disposal_token(asset: str, method: str, disposal_date: str, amount: float, issued_at: int) -> str:
    return compute_token(
        kind="disposal",
        asset=asset,
        method=method,
        disposal_date=disposal_date,
        amount=round(float(amount), 2),
        issued_at=int(issued_at),
    )



def is_fresh(issued_at: int, max_age_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
             now: int = None) -> bool:
    """True if issued_at is within [now - max_age_seconds, now + skew-tolerance].

    Rejects both stale tokens (replay of an old render/confirm) and
    implausibly-future ones (clock manipulation / fabricated issued_at).
    """
    now = int(now) if now is not None else int(time.time())
    age = now - int(issued_at)
    return -CLOCK_SKEW_TOLERANCE_SECONDS <= age <= max_age_seconds
