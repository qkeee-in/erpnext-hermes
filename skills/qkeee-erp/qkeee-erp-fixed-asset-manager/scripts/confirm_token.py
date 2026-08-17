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
"""

import hashlib
import json


def compute_token(**fields) -> str:
    """Deterministic short token over the given financial facts."""
    canonical = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def depreciation_run_token(asset: str, asset_depr_schedule: str, as_of_date: str,
                            total_depreciation: float) -> str:
    return compute_token(
        kind="depreciation_run",
        asset=asset,
        asset_depr_schedule=asset_depr_schedule,
        as_of_date=as_of_date,
        total_depreciation=round(float(total_depreciation), 2),
    )


def disposal_token(asset: str, method: str, disposal_date: str, amount: float) -> str:
    return compute_token(
        kind="disposal",
        asset=asset,
        method=method,
        disposal_date=disposal_date,
        amount=round(float(amount), 2),
    )
