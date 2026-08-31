#!/usr/bin/env python3
"""
qkeee-erp-associate — fixed-assets domain (Asset lifecycle: depreciation,
disposal, transfer).

Ported from qkeee-erp-fixed-asset-manager/scripts/erp_client.py during
Phase 1 (connector consolidation): mutate_resource_with_concurrency() and
call_whitelisted_method() (plus its _expected_token() helper) below are
this skill's genuine business logic beyond the shared core set.

AUDIT GAP CLOSED (per the refactor plan's Risks section, "Fix during
Phase 1, don't carry the gap forward"): the old erp_client.py's
call_whitelisted_method() never wrote to Qkeee Bot Audit Log — depreciation
runs, scraps, and disposals were unaudited there even though they posted
the usual ERPNext Comment. call_whitelisted_method() below now wraps the
RPC call with the same two-phase record_audit_log_start()/
record_audit_log_finish() every other write path gets, closing that gap
instead of carrying it forward into the consolidated connector.

SCOPE NOTE on confirm_token.py: _expected_token()'s token constructors
(depreciation_run_token, disposal_token) originally lived in this skill's
OWN confirm_token.py, a sibling file to erp_client.py that Phase 1's task
was not scoped to consolidate (only the ten erp_client.py copies were
diffed/ported — see the consolidation report). Rather than leave this
module non-functional pending a separate confirm_token.py consolidation
pass, its two small token constructors are carried here verbatim,
alongside their consumer. This is a deliberate Phase 1 scope call, not an
oversight — flag it in Phase 2 review.

ALLOWED_WRITE_DOCTYPES: "Asset" (create/update/submit/cancel via
render_asset_draft.py/render_movement_draft.py) plus "Asset Movement" and
"Asset Repair", inferred from references/ and render_movement_draft.py.
Depreciation/scrap/restore/disposal go through call_whitelisted_method()
below, which bypasses mutate_resource()'s generic action set (and
therefore this allowlist) entirely, same as in the original design —
those four RPCs are individually named/gated (WHITELISTED_METHODS,
TOKEN_REQUIRED_METHODS), not doctype-allowlisted.
"""

import hashlib
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client
from core.client import (
    ConnectorError, ReadOnlyModeError, MissingRequesterError, StaleConfirmationError,
    SKILL_LABEL, get_env_config, get_resource, mutate_resource, record_comment,
    record_audit_log_start, record_audit_log_finish, _request,
)
from core.confirm_token import is_fresh

DOMAIN_NAME = "fixed_assets"

ALLOWED_WRITE_DOCTYPES = (
    "Asset",
    "Asset Movement",
    "Asset Repair",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """This domain's write entry point — plain mutate_resource() gated by
    ALLOWED_WRITE_DOCTYPES above (domain="fixed_assets")."""
    return core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)


# --------------------------------------------------------------------------
# Token constructors — see module docstring's "SCOPE NOTE on confirm_token.py"
# --------------------------------------------------------------------------

def _compute_token(**fields) -> str:
    canonical = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def depreciation_run_token(asset: str, asset_depr_schedule: str, as_of_date: str,
                            total_depreciation: float, issued_at: int) -> str:
    return _compute_token(
        kind="depreciation_run",
        asset=asset,
        asset_depr_schedule=asset_depr_schedule,
        as_of_date=as_of_date,
        total_depreciation=round(float(total_depreciation), 2),
        issued_at=int(issued_at),
    )


def disposal_token(asset: str, method: str, disposal_date: str, amount: float, issued_at: int) -> str:
    return _compute_token(
        kind="disposal",
        asset=asset,
        method=method,
        disposal_date=disposal_date,
        amount=round(float(amount), 2),
        issued_at=int(issued_at),
    )


# --------------------------------------------------------------------------
# Genuine per-skill business logic, ported from erp_client.py
# --------------------------------------------------------------------------

def mutate_resource_with_concurrency(tag: str, doctype: str, action: str, payload: dict = None,
                                      name: str = None, mode: str = "read-only",
                                      expected_modified: str = None, requested_by: str = None,
                                      skip_comment: bool = False,
                                      *, session_id: str = None, domain_code: str = None,
                                      user_approved: bool = False, approval_note: str = None) -> dict:
    """TOCTOU-checked wrapper around the shared core.client.mutate_resource()
    — this domain's own extension for the submit-time concurrency check
    (Asset capitalization / depreciation-run / disposal review steps).

    `expected_modified` (submit only): if the caller knows the record's
    `modified` timestamp from when it last read/staged the draft, pass it
    here. Before delegating to mutate_resource()'s submit path, this
    re-fetches the record and refuses (raising ConnectorError, not
    silently proceeding) if `modified` has moved on since — someone else
    edited the record between staging and submit. This narrows (does not
    eliminate) the fetch-then-submit TOCTOU gap; the remaining unmitigated
    window between this check and the submit POST itself is what
    frappe.client.submit's own TimestampMismatchError backstops.

    Every other action, and `submit` with `expected_modified` omitted,
    passes straight through to mutate_resource() unchanged — this wrapper
    adds a pre-check, it never re-implements the write itself.
    """
    if action == "submit" and expected_modified is not None:
        if not name:
            raise ConnectorError("submit requires a record 'name'.")
        current = get_resource(tag, doctype, name, strip_noise=False).get("data") or {}
        if current.get("modified") != expected_modified:
            raise ConnectorError(
                f"'{doctype}' '{name}' was modified since it was last staged "
                f"(expected modified={expected_modified!r}, now {current.get('modified')!r}). "
                f"Someone else may have changed it — re-review before submitting."
            )
    return mutate_resource(tag, doctype, action, payload, name, mode, requested_by,
                            skip_comment=skip_comment, domain=DOMAIN_NAME,
                            session_id=session_id, domain_code=domain_code,
                            user_approved=user_approved, approval_note=approval_note)


WHITELISTED_METHODS = {
    "make_depreciation_entry": "/api/method/erpnext.assets.doctype.asset.depreciation.make_depreciation_entry",
    "scrap_asset": "/api/method/erpnext.assets.doctype.asset.depreciation.scrap_asset",
    "restore_asset": "/api/method/erpnext.assets.doctype.asset.depreciation.restore_asset",
    "make_sales_invoice": "/api/method/erpnext.assets.doctype.asset.asset.make_sales_invoice",
}

# These three carry the domain's double-confirm non-negotiable (depreciation
# runs and disposals) — restore_asset is a recovery action, not a
# write-off/posting action, so it isn't token-gated.
TOKEN_REQUIRED_METHODS = {"make_depreciation_entry", "scrap_asset", "make_sales_invoice"}


def call_whitelisted_method(tag: str, method: str, body: dict, mode: str = "read-only",
                             confirmation_token: str = None, token_facts: dict = None,
                             requested_by: str = None, *, session_id: str = None,
                             domain_code: str = None) -> dict:
    """Call one of the four domain-specific whitelisted RPC methods.

    `body` is sent to ERPNext verbatim as the RPC's actual arguments —
    only the exact fields that method's real signature accepts, nothing
    more.

    These bypass mutate_resource()'s generic create/update/submit/cancel
    action set (they don't fit it, so ALLOWED_WRITE_DOCTYPES doesn't apply
    here either), but enforce the same `mode == "read-write"` and
    `requested_by` gates in code, exactly like mutate_resource().

    For the three double-confirm methods (make_depreciation_entry,
    scrap_asset, make_sales_invoice), `confirmation_token` is also
    required and must match the token a render script computed from the
    same financial facts (depreciation_run_token()/disposal_token()
    above). `token_facts` carries the identifying facts needed to
    recompute that token — deliberately kept separate from `body` so
    verification-only facts never leak into the actual API payload sent
    to ERPNext.

    On success, posts a best-effort audit Comment onto the relevant Asset
    record naming the requester. AUDIT GAP CLOSED (Phase 1, per the
    consolidation plan's Risks section): this call is now also wrapped in
    the same two-phase Qkeee Bot Audit Log logging every other write path
    gets — the old erp_client.py copy bypassed mutate_resource() entirely
    for this RPC shape and never logged it there, which the plan flags as
    a gap to fix in this phase, not carry forward.
    """
    if method not in WHITELISTED_METHODS:
        raise ConnectorError(
            f"Unknown whitelisted method '{method}'. Expected one of {sorted(WHITELISTED_METHODS)}."
        )
    if mode != "read-write":
        raise ReadOnlyModeError(
            f"Refusing '{method}': qkeee_erp.mode is '{mode}', not 'read-write'. "
            f"Switch modes explicitly if this write is intended."
        )
    if not requested_by:
        raise MissingRequesterError(
            f"Refusing '{method}': requested_by is missing. Set qkeee_erp.requested_by to the "
            f"ERPNext user id/email of the person requesting this change."
        )
    if method in TOKEN_REQUIRED_METHODS:
        if not confirmation_token:
            raise ConnectorError(
                f"'{method}' requires confirmation_token — render the double-confirm "
                f"output first and pass its exact token here. This method cannot be "
                f"called without one."
            )
        issued_at = (token_facts or {}).get("issued_at")
        if not issued_at:
            raise ConnectorError(
                f"'{method}' requires token_facts['issued_at'] — the render script "
                f"surfaces this alongside the confirmation token; pass it through unchanged."
            )
        expected = _expected_token(method, token_facts or {})
        if confirmation_token != expected:
            raise ConnectorError(
                f"confirmation_token does not match token_facts for '{method}' — re-render "
                f"the confirmation against the current data and use that token, rather than "
                f"reusing an older one."
            )
        if not is_fresh(issued_at):
            raise StaleConfirmationError(
                f"confirmation_token for '{method}' has expired (older than 15 minutes) — "
                f"re-render the confirmation against current data before executing."
            )

    cfg = get_env_config(tag)
    asset_name = (body or {}).get("asset_name")
    audit_log_name = record_audit_log_start(
        cfg, action=method, doctype="Asset", name=asset_name, requested_by=requested_by,
        session_id=session_id, domain_code=domain_code,
        user_approved=method in TOKEN_REQUIRED_METHODS,
        approval_note=f"call_whitelisted_method: {method}",
    )
    try:
        result = _request(cfg, "POST", WHITELISTED_METHODS[method], payload=body)
    except ConnectorError as e:
        record_audit_log_finish(cfg, audit_log_name, status="Failure", error_detail=str(e))
        raise
    if asset_name:
        record_comment(
            cfg, "Asset", asset_name,
            f"[{SKILL_LABEL}/{DOMAIN_NAME}] {method} — requested by {requested_by}, applied via qkeee-erp bot.",
        )
    record_audit_log_finish(cfg, audit_log_name, status="Success", reference_name=asset_name)
    return result


def _expected_token(method: str, facts: dict) -> str:
    issued_at = facts.get("issued_at", 0)
    if method == "make_depreciation_entry":
        return depreciation_run_token(
            asset=facts.get("asset", ""),
            asset_depr_schedule=facts.get("asset_depr_schedule", ""),
            as_of_date=facts.get("as_of_date", ""),
            total_depreciation=facts.get("total_depreciation", 0),
            issued_at=issued_at,
        )
    if method == "scrap_asset":
        return disposal_token(
            asset=facts.get("asset", ""), method="scrap",
            disposal_date=facts.get("disposal_date", ""),
            amount=facts.get("amount", 0),
            issued_at=issued_at,
        )
    if method == "make_sales_invoice":
        return disposal_token(
            asset=facts.get("asset", ""), method="sale",
            disposal_date=facts.get("disposal_date", ""),
            amount=facts.get("amount", 0),
            issued_at=issued_at,
        )
    raise ConnectorError(f"No token scheme defined for '{method}'.")
