#!/usr/bin/env python3
"""
qkeee-erp-inventory connector — read+write copy of the canonical
qkeee-erp-core ERPNext (Frappe REST API) client, per the self-contained-
copies architecture decision. This persona is read-write-capable (gated
by qkeee_erp.mode): stock transfers, stock reconciliations, Material
Request drafting.

Self-contained: stdlib only (urllib), no third-party deps.

Env/credential model (tagged, not fixed dev/test/qa/prod):
  QKEEE_ERP_<TAG>_BASE_URL
  QKEEE_ERP_<TAG>_API_KEY
  QKEEE_ERP_<TAG>_API_SECRET

<TAG> defaults to "DEFAULT" if the user didn't name one at install.
Active tag + read-only/read-write mode are non-secret and live in
metadata.hermes.config (qkeee_erp.active_env, qkeee_erp.mode), passed
in here via CLI flags / env — never hardcoded.

Non-negotiable: never issue a write call while mode == "read-only".
This is enforced in mutate_resource() below, not just in the calling
skill's prompt. This skill's own non-negotiable (stock transfers and
reconciliations never adjust silently) lives outside this file, closer
to where each draft is built: scripts/render_stock_entry_draft.py and
scripts/render_reconciliation_draft.py.

Audit-trail retrofit: every write is additionally logged to Qkeee Bot
Audit Log (two-phase, best-effort — see
qkeee-erp-bot-init/references/bot-doctypes-design.md).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Audit-comment attribution: every write posts a Comment naming the
# requester onto the affected record, so ERPNext's own audit trail
# shows who asked, not just that the shared bot account acted.
SKILL_LABEL = "qkeee-erp-inventory"

AUDIT_LOG_DOCTYPE = "Qkeee Bot Audit Log"
SESSION_DOCTYPE = "Qkeee Bot Session"
MESSAGE_DOCTYPE = "Qkeee Bot Message"
PERSONA_DOCTYPE = "Qkeee Bot Persona"
AUDIT_EXEMPT_DOCTYPES = {
    AUDIT_LOG_DOCTYPE, SESSION_DOCTYPE, MESSAGE_DOCTYPE, PERSONA_DOCTYPE,
    "Comment",
}


class ConnectorError(Exception):
    """Raised for missing config / auth / HTTP failures with a specific, actionable message."""


class ReadOnlyModeError(ConnectorError):
    """Raised when a write call is attempted while qkeee_erp.mode == read-only."""


class MissingRequesterError(ConnectorError):
    """Raised when a write call is attempted without a requested_by identity."""


def _tag_env_var(tag: str, suffix: str) -> str:
    sanitized = "".join(c if c.isalnum() else "_" for c in tag.upper()) or "DEFAULT"
    return f"QKEEE_ERP_{sanitized}_{suffix}"


def get_env_config(tag: str = "default") -> dict:
    """Resolve base_url/api_key/api_secret for a given environment tag.

    Fails with a specific "missing QKEEE_ERP_<TAG>_API_KEY" style error,
    never a generic auth failure.
    """
    base_url = os.environ.get(_tag_env_var(tag, "BASE_URL"))
    api_key = os.environ.get(_tag_env_var(tag, "API_KEY"))
    api_secret = os.environ.get(_tag_env_var(tag, "API_SECRET"))

    missing = [
        name
        for name, val in (
            (_tag_env_var(tag, "BASE_URL"), base_url),
            (_tag_env_var(tag, "API_KEY"), api_key),
            (_tag_env_var(tag, "API_SECRET"), api_secret),
        )
        if not val
    ]
    if missing:
        raise ConnectorError(
            f"Missing environment variable(s) for tag '{tag}': {', '.join(missing)}. "
            f"Set them in your shell profile / OS credential manager, then retry."
        )

    return {
        "tag": tag,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "api_secret": api_secret,
    }


def _request(cfg: dict, method: str, path: str, params: dict = None, payload: dict = None) -> dict:
    url = cfg["base_url"] + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {cfg['api_key']}:{cfg['api_secret']}")
    req.add_header("Content-Type", "application/json")
    # Python's default urllib UA ("Python-urllib/x.y") is known to be
    # blocked by common WAF/bot-protection (e.g. Cloudflare) fronting some
    # production ERPNext instances, returning a 403 that looks like an auth
    # failure but isn't. Finding originates from a different qkeee-erp-*
    # skill's build (see qkeee-erp-core/references/connector-reference.md)
    # — send an explicit UA regardless, since it's a no-cost precaution.
    req.add_header("User-Agent", "qkeee-erp-inventory/1.0")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ConnectorError(
            f"ERPNext API error ({e.code}) on {method} {path} against '{cfg['tag']}' "
            f"({cfg['base_url']}): {body[:500]}"
        ) from e
    except urllib.error.URLError as e:
        raise ConnectorError(
            f"Could not reach '{cfg['tag']}' ({cfg['base_url']}): {e.reason}. "
            f"Check the base URL and network connectivity."
        ) from e


def health_check(tag: str = "default") -> dict:
    """Verify active environment is reachable and authenticated.

    Confirms connectivity + valid credentials only — not query/write-time
    permission on any specific DocType. Report a later permission error
    (403/PermissionError) as its own distinct failure mode, not folded
    into a connectivity failure.
    """
    cfg = get_env_config(tag)
    result = _request(cfg, "GET", "/api/method/frappe.auth.get_logged_user")
    return {"tag": tag, "base_url": cfg["base_url"], "status": "ok", "logged_in_as": result.get("message")}


def query_resource(tag: str, doctype: str, filters: list = None, fields: list = None, limit: int = 20,
                    *, debug: bool = False, session_id: str = None, persona_code: str = None,
                    requested_by: str = None) -> dict:
    """Generic resource query — read any DocType with filters/fields.

    Fetches one extra row beyond `limit` to detect truncation, then trims
    back to `limit` — callers get an explicit `has_more` flag instead of a
    result set that's silently incomplete.

    `debug=True` additionally logs this read to Qkeee Bot Audit Log
    (best-effort).
    """
    cfg = get_env_config(tag)
    params = {"limit_page_length": limit + 1}
    if filters:
        params["filters"] = json.dumps(filters)
    if fields:
        params["fields"] = json.dumps(fields)
    path = f"/api/resource/{urllib.parse.quote(doctype)}"
    result = _request(cfg, "GET", path, params=params)
    rows = result.get("data", [])
    has_more = len(rows) > limit

    if debug:
        _log_read(cfg, doctype, None, requested_by, session_id, persona_code)

    return {"data": rows[:limit], "has_more": has_more, "limit": limit}


def get_stock_reconciliation_items(tag: str, warehouse: str, company: str,
                                    posting_date: str, item_code: str = None,
                                    posting_time: str = "23:59:59") -> dict:
    """Resolve authoritative current qty/valuation (and, for batch-tracked
    items, per-batch rows) for a warehouse/item via ERPNext's own
    get_items whitelisted method — NEVER guess or hand-supply current_qty
    for a Stock Reconciliation line.

    Confirmed live (<erp-instance>): the Stock Reconciliation
    Item's `current_qty` field is NOT resolved server-side from a
    caller-supplied value at create time (it's silently reset to 0 in the
    create response regardless of what's passed in), and Bin.actual_qty is
    NOT authoritative for a batch-tracked item — get_items() is the only
    confirmed-correct source, and for a batch-tracked item it returns one
    row PER EXISTING BATCH (not a single item-level total), each with its
    own batch_no + current_qty. A caller that reconciles a batch-tracked
    item without resolving per-batch rows first and instead submits a
    single line with an unresolved/zero current_qty risks ERPNext creating
    a brand-new batch for the delta instead of correcting existing
    batches — confirmed live: this exact mistake inflated a 6-unit balance
    to 14 units (should have been corrected to 8) because the reconcile
    silently ADDED 8 to the existing balance rather than SETTING it,
    when current_qty was passed as 0/unresolved for a batch-tracked item.

    `item_code` is optional (confirmed live): omitting it
    returns every item with a nonzero/tracked balance in `warehouse`, not
    just one — useful for reconciling a whole warehouse's physical count
    in one resolver call instead of one per item.
    """
    cfg = get_env_config(tag)
    payload = {
        "warehouse": warehouse,
        "posting_date": posting_date,
        "posting_time": posting_time,
        "company": company,
    }
    if item_code:
        payload["item_code"] = item_code
    result = _request(
        cfg, "POST",
        "/api/method/erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_items",
        payload=payload,
    )
    rows = result.get("message", [])
    return {"item_code": item_code, "warehouse": warehouse, "rows": rows, "batch_tracked": any(r.get("batch_no") for r in rows)}


def bin_rows_to_actual_source_qty(bin_rows: list) -> dict:
    """Convert get_bin_qty()'s ``{"data": [{"item_code", "warehouse",
    "actual_qty"}, ...]}`` row shape into the ``{(item_code, warehouse):
    qty}`` mapping render_stock_entry_draft.render_stock_entry_draft()
    expects as `actual_source_qty`.

    Added because this translation was previously left undocumented and
    untested, entirely up to whatever called get_bin_qty() to get the
    shape right at runtime — a caller passing get_bin_qty()'s raw ``data``
    list straight into actual_source_qty would fail every availability
    check with "no actual_source_qty entry provided", not because stock
    was actually unavailable but because the shapes never matched.
    """
    return {(row["item_code"], row["warehouse"]): row["actual_qty"] for row in bin_rows}


def get_bin_qty(tag: str, item_code: str, warehouse: str = None) -> dict:
    """Read the live Bin (actual on-hand qty) for an item, optionally
    scoped to one warehouse. Authoritative for non-batch/non-serial items;
    for batch-tracked items prefer get_stock_reconciliation_items() to
    also see the per-batch breakdown before staging a reconciliation.
    """
    cfg = get_env_config(tag)
    filters = [["item_code", "=", item_code]]
    if warehouse:
        filters.append(["warehouse", "=", warehouse])
    params = {"filters": json.dumps(filters), "fields": json.dumps(["item_code", "warehouse", "actual_qty"]),
              "limit_page_length": 100}
    result = _request(cfg, "GET", "/api/resource/Bin", params=params)
    return {"data": result.get("data", [])}


# Fields stripped from get_resource() output: audit/system metadata and
# presentation-only HTML/display fields that no review or reporting logic
# in this skill reads. Never strips Link fields, child tables, or anything
# review steps check for validity. Measured live against <erp-instance>
# (Sales Order doc, same field shapes recur across ERPNext doctypes):
# ~38% byte reduction.
_NOISE_FIELDS = {
    "owner", "creation", "modified", "modified_by", "idx", "naming_series",
    "title", "other_charges_calculation", "terms", "address_display",
    "shipping_address", "company_address_display", "in_words",
    "base_in_words", "language", "doctype", "parentfield", "parenttype",
}


def _strip_noise(obj):
    if isinstance(obj, dict):
        return {k: _strip_noise(v) for k, v in obj.items()
                if k not in _NOISE_FIELDS and v not in (None, "")}
    if isinstance(obj, list):
        return [_strip_noise(x) for x in obj]
    return obj


def get_resource(tag: str, doctype: str, name: str, strip_noise: bool = True,
                  *, debug: bool = False, session_id: str = None, persona_code: str = None,
                  requested_by: str = None) -> dict:
    """Single-resource full-doc GET — the only way to get child-table rows
    (Frappe's list API silently drops Table fields even when requested via
    `fields`; the single-resource GET ignores `fields` entirely and always
    returns everything). Use only when child-table Link validity actually
    needs checking (e.g. Stock Entry/Reconciliation line-item review
    before submit) — for status/report reads that don't need child
    tables, use query_resource() with --filters + --fields instead, it's
    ~25x cheaper.

    strip_noise=True (default) drops audit/system metadata and
    presentation-only HTML fields before returning — see _NOISE_FIELDS.

    `debug=True` logs this read to Qkeee Bot Audit Log, same as
    query_resource().
    """
    cfg = get_env_config(tag)
    path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
    result = _request(cfg, "GET", path)
    data = result.get("data")
    if strip_noise and data is not None:
        data = _strip_noise(data)

    if debug:
        _log_read(cfg, doctype, name, requested_by, session_id, persona_code)

    return {"data": data}


def record_comment(cfg: dict, doctype: str, name: str, content: str) -> bool:
    """Best-effort: post a Comment onto an ERPNext record via
    frappe.desk.form.utils.add_comment, so the audit trail lives in
    ERPNext itself, not only in this session's chat transcript. Never
    raises — a comment failure must not block or roll back the actual
    write it's documenting. Returns True on success, False on failure."""
    try:
        _request(cfg, "POST", "/api/method/frappe.desk.form.utils.add_comment", payload={
            "reference_doctype": doctype,
            "reference_name": name,
            "content": content,
        })
        return True
    except ConnectorError:
        return False


# --------------------------------------------------------------------------
# Audit logging (Qkeee Bot Audit Log)
# Best-effort throughout: if the target instance hasn't run
# qkeee-erp-bot-init yet, every function below swallows the failure and the
# caller's real ERPNext read/write proceeds unaffected.
# --------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _session_or_fallback(session_id: str) -> str:
    """`session` is a mandatory field on Qkeee Bot Audit Log. Callers that
    never got/passed a real session_id (e.g. CLI invocations without
    --session-id) must still produce a non-empty value here — an empty
    string fails Audit Log's mandatory-field validation, and because
    _audit_insert() swallows all exceptions by design, that failure is
    otherwise invisible (the row is just silently never written). Same
    `local-<timestamp>` fallback shape as open_session()'s own fallback."""
    return session_id or f"local-{_now_iso()}"


def _diff_fields(before: dict, after: dict) -> list:
    if not before or not after:
        return []
    keys = (set(before.keys()) | set(after.keys())) - _NOISE_FIELDS
    diff = []
    for k in sorted(keys):
        old, new = before.get(k), after.get(k)
        if old != new:
            diff.append({"fieldname": k, "old": old, "new": new})
    return diff


def _audit_insert(cfg: dict, fields: dict) -> str:
    try:
        payload = {"doctype": AUDIT_LOG_DOCTYPE, **fields}
        result = _request(cfg, "POST", f"/api/resource/{urllib.parse.quote(AUDIT_LOG_DOCTYPE)}", payload=payload)
        return (result.get("data") or {}).get("name")
    except Exception as e:
        print(f"WARN: audit log insert failed (non-fatal): {e}", file=sys.stderr)
        return None


def _audit_update(cfg: dict, log_name: str, fields: dict) -> bool:
    if not log_name:
        return False
    try:
        path = f"/api/resource/{urllib.parse.quote(AUDIT_LOG_DOCTYPE)}/{urllib.parse.quote(log_name)}"
        _request(cfg, "PUT", path, payload=fields)
        return True
    except Exception as e:
        print(f"WARN: audit log update failed (non-fatal): {e}", file=sys.stderr)
        return False


def _audit_submit(cfg: dict, log_name: str) -> bool:
    try:
        path = f"/api/resource/{urllib.parse.quote(AUDIT_LOG_DOCTYPE)}/{urllib.parse.quote(log_name)}"
        existing = _request(cfg, "GET", path)
        full_doc = existing.get("data")
        if not full_doc:
            return False
        _request(cfg, "POST", "/api/method/frappe.client.submit", payload={"doc": full_doc})
        return True
    except Exception as e:
        print(f"WARN: audit log submit failed (non-fatal): {e}", file=sys.stderr)
        return False


def _log_read(cfg: dict, doctype: str, name: str, requested_by: str, session_id: str, persona_code: str) -> None:
    if doctype in AUDIT_EXEMPT_DOCTYPES:
        return
    _audit_insert(cfg, {
        "session": _session_or_fallback(session_id),
        "persona_code": persona_code or SKILL_LABEL,
        "environment_tag": cfg.get("tag", ""),
        "action": "Read",
        "reference_doctype": doctype,
        "reference_name": name or "",
        "requested_by": requested_by or "",
        "timestamp": _now_iso(),
        "status": "Success",
        "user_approved": "Not Required",
    })


def record_audit_log_start(cfg: dict, *, action: str, doctype: str, name: str, requested_by: str,
                            session_id: str = None, persona_code: str = None,
                            payload_before: dict = None, user_approved: bool = False,
                            approval_note: str = None) -> str:
    if doctype in AUDIT_EXEMPT_DOCTYPES:
        return None
    return _audit_insert(cfg, {
        "session": _session_or_fallback(session_id),
        "persona_code": persona_code or SKILL_LABEL,
        "environment_tag": cfg.get("tag", ""),
        "action": action,
        "reference_doctype": doctype,
        "reference_name": name or "",
        "requested_by": requested_by or "",
        "timestamp": _now_iso(),
        "status": "Attempted",
        "payload_before": json.dumps(payload_before) if payload_before else None,
        "user_approved": "Approved" if user_approved else "Not Confirmed",
        "approval_note": approval_note,
    })


def record_audit_log_finish(cfg: dict, log_name: str, *, status: str, reference_name: str = None,
                             payload_before: dict = None, payload_after: dict = None,
                             error_detail: str = None, audit_comment_posted: bool = None) -> None:
    if not log_name:
        return
    fields = {"status": status, "timestamp": _now_iso()}
    if reference_name:
        fields["reference_name"] = reference_name
    if payload_after is not None:
        fields["payload_after"] = json.dumps(payload_after)
        diff = _diff_fields(payload_before, payload_after)
        if diff:
            fields["field_diff"] = json.dumps(diff)
    if error_detail:
        fields["error_detail"] = error_detail[:1900]
    if audit_comment_posted is not None:
        fields["audit_comment_posted"] = 1 if audit_comment_posted else 0
    if _audit_update(cfg, log_name, fields):
        _audit_submit(cfg, log_name)


def mutate_resource(tag: str, doctype: str, action: str, payload: dict = None,
                     name: str = None, mode: str = "read-only", requested_by: str = None,
                     *, session_id: str = None, persona_code: str = None,
                     user_approved: bool = False, approval_note: str = None) -> dict:
    """Generic resource mutate — create/update/submit/cancel a DocType record.

    `mode` must be passed explicitly by the caller (sourced from
    metadata.hermes.config qkeee_erp.mode) — this function refuses to
    guess a safe default and refuses to write unless mode == "read-write".
    This is the library-wide mode gate. It does NOT know about this
    skill's capability-specific gate (stated-impact-before-confirm for
    stock transfers/reconciliations) — that lives in
    render_stock_entry_draft.py / render_reconciliation_draft.py, not here.

    `user_approved` should be True only when this write's confirm stage
    actually ran with the user first — logged to Qkeee Bot Audit Log for
    later scanning, not enforced as a gate here.
    """
    if mode != "read-write":
        raise ReadOnlyModeError(
            f"Refusing {action} on '{doctype}': qkeee_erp.mode is '{mode}', not 'read-write'. "
            f"Switch modes explicitly if this write is intended."
        )
    if not requested_by:
        raise MissingRequesterError(
            f"Refusing {action} on '{doctype}': requested_by is missing. "
            f"Set qkeee_erp.requested_by to the ERPNext user id/email of the person requesting this change."
        )

    cfg = get_env_config(tag)

    payload_before = None
    if action == "update" and doctype not in AUDIT_EXEMPT_DOCTYPES and name:
        try:
            payload_before = get_resource(tag, doctype, name, strip_noise=False).get("data")
        except ConnectorError:
            payload_before = None

    audit_log_name = record_audit_log_start(
        cfg, action=action.capitalize(), doctype=doctype, name=name, requested_by=requested_by,
        session_id=session_id, persona_code=persona_code, payload_before=payload_before,
        user_approved=user_approved, approval_note=approval_note,
    )

    try:
        result = _do_mutate(cfg, doctype, action, payload, name, requested_by)
    except ConnectorError as e:
        record_audit_log_finish(cfg, audit_log_name, status="Failure", error_detail=str(e))
        raise

    data = result.get("data") if isinstance(result, dict) else None
    if data is None and isinstance(result, dict):
        data = result.get("message")
    reference_name = (data or {}).get("name") if isinstance(data, dict) else name
    audit_comment_posted = result.pop("_audit_comment_posted", None) if isinstance(result, dict) else None
    record_audit_log_finish(
        cfg, audit_log_name, status="Success", reference_name=reference_name,
        payload_before=payload_before, payload_after=data if isinstance(data, dict) else None,
        audit_comment_posted=audit_comment_posted,
    )
    return result


def _do_mutate(cfg: dict, doctype: str, action: str, payload: dict, name: str, requested_by: str) -> dict:
    if action == "create":
        path = f"/api/resource/{urllib.parse.quote(doctype)}"
        result = _request(cfg, "POST", path, payload=payload)
        created_name = (result.get("data") or {}).get("name")
        if created_name:
            result["_audit_comment_posted"] = record_comment(
                cfg, doctype, created_name,
                f"[{SKILL_LABEL}] created — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        return result
    if action == "update":
        if not name:
            raise ConnectorError("update requires a record 'name'.")
        path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
        result = _request(cfg, "PUT", path, payload=payload)
        result["_audit_comment_posted"] = record_comment(
            cfg, doctype, name,
            f"[{SKILL_LABEL}] updated — requested by {requested_by}, applied via qkeee-erp bot.",
        )
        return result
    if action == "submit":
        if not name:
            raise ConnectorError("submit requires a record 'name'.")
        # frappe.client.submit builds its doc via frappe.get_doc(dict) — a
        # sparse {doctype, name} payload has no DB-loaded field values, so
        # validate() fails mandatory-field checks. Fetch the full record
        # first, then submit that. Known gap, not fixed here: this
        # GET-then-POST is two calls, not one — a record edited by someone
        # else in between could get submitted with a stale full_doc. Flag
        # an unexpected TimestampMismatchError on submit rather than
        # treating it as a generic write failure.
        get_path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
        existing = _request(cfg, "GET", get_path)
        full_doc = existing.get("data")
        if not full_doc:
            raise ConnectorError(f"Could not load '{doctype}' '{name}' before submit — nothing to submit.")
        result = _request(cfg, "POST", "/api/method/frappe.client.submit", payload={"doc": full_doc})
        result["_audit_comment_posted"] = record_comment(
            cfg, doctype, name,
            f"[{SKILL_LABEL}] submitted — requested by {requested_by}, applied via qkeee-erp bot.",
        )
        return result
    if action == "cancel":
        if not name:
            raise ConnectorError("cancel requires a record 'name'.")
        body = {"doctype": doctype, "name": name}
        result = _request(cfg, "POST", "/api/method/frappe.client.cancel", payload=body)
        result["_audit_comment_posted"] = record_comment(
            cfg, doctype, name,
            f"[{SKILL_LABEL}] cancelled — requested by {requested_by}, applied via qkeee-erp bot.",
        )
        return result
    if action == "delete":
        if not name:
            raise ConnectorError("delete requires a record 'name'.")
        comment_posted = record_comment(
            cfg, doctype, name,
            f"[{SKILL_LABEL}] deleted — requested by {requested_by}, applied via qkeee-erp bot.",
        )
        path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
        result = _request(cfg, "DELETE", path)
        if not isinstance(result, dict):
            result = {}
        result["_audit_comment_posted"] = comment_posted
        return result

    raise ConnectorError(f"Unknown action '{action}'. Expected create/update/submit/cancel/delete.")


def list_configured_tags() -> list:
    """List environment tags with a full var set (BASE_URL+API_KEY+API_SECRET)
    already present in os.environ."""
    tags = {}
    for var_name in os.environ:
        if not var_name.startswith("QKEEE_ERP_"):
            continue
        for suffix in ("_BASE_URL", "_API_KEY", "_API_SECRET"):
            if var_name.endswith(suffix):
                tag = var_name[len("QKEEE_ERP_"):-len(suffix)]
                tags.setdefault(tag, set()).add(suffix)
                break
    return sorted(tag for tag, found in tags.items() if found == {"_BASE_URL", "_API_KEY", "_API_SECRET"})


def _cli():
    p = argparse.ArgumentParser(description="qkeee-erp-inventory connector CLI")
    p.add_argument("--tag", help="environment tag, from qkeee_erp.active_env (required for health/query/mutate/etc.)")
    p.add_argument("--mode", choices=["read-only", "read-write"],
                   help="from qkeee_erp.mode (required for mutate)")
    p.add_argument("--requested-by",
                   help="ERPNext user id/email of the human requesting the change, "
                        "from qkeee_erp.requested_by (required for mutate)")
    p.add_argument("--debug", action="store_true", help="from qkeee_erp.debug — logs reads to Qkeee Bot Audit Log")
    p.add_argument("--session-id", help="from the caller's open_session()")
    p.add_argument("--persona-code", default=SKILL_LABEL, help="threaded into audit rows")
    p.add_argument("--user-approved", action="store_true",
                   help="pass only if this write's confirm stage actually ran with the user first (mutate only)")
    p.add_argument("--approval-note", help="free text of what was confirmed (mutate only)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("list-envs")

    q = sub.add_parser("query")
    q.add_argument("doctype")
    q.add_argument("--filters", help="JSON list, e.g. '[[\"item_code\",\"=\",\"SKU-1\"]]'")
    q.add_argument("--fields", help="JSON list, e.g. '[\"name\",\"item_code\"]'")
    q.add_argument("--limit", type=int, default=20)

    bq = sub.add_parser("bin-qty", help="Live Bin actual_qty for an item, optionally per warehouse")
    bq.add_argument("item_code")
    bq.add_argument("--warehouse")

    ri = sub.add_parser("recon-items", help="Authoritative current qty/valuation (get_items) for a reconciliation line, or a whole warehouse if item_code omitted")
    ri.add_argument("item_code", nargs="?", default=None, help="omit to get every item with a balance in --warehouse")
    ri.add_argument("--warehouse", required=True)
    ri.add_argument("--company", required=True)
    ri.add_argument("--posting-date", required=True)
    ri.add_argument("--posting-time", default="23:59:59")

    g = sub.add_parser("get", help="Single-resource full-doc GET (includes child tables) — noise-stripped by default")
    g.add_argument("doctype")
    g.add_argument("name")
    g.add_argument("--no-strip", action="store_true", help="skip noise-stripping, return the raw doc verbatim")

    m = sub.add_parser("mutate")
    m.add_argument("doctype")
    m.add_argument("action", choices=["create", "update", "submit", "cancel", "delete"])
    m.add_argument("--payload", help="JSON object for create/update, as a literal arg. "
                                      "Prefer --payload-file: this lands in shell history "
                                      "and process listings (ps), which --payload-file avoids.")
    m.add_argument("--payload-file", help="path to a JSON file for create/update — preferred over --payload")
    m.add_argument("--name", help="record name, required for update/submit/cancel/delete")

    args = p.parse_args()

    if args.command in ("health", "query", "get", "bin-qty", "recon-items", "mutate") and not args.tag:
        p.error(f"--tag is required for '{args.command}'")
    if args.command == "mutate" and not args.mode:
        p.error("--mode is required for 'mutate'")
    if args.command == "mutate" and not args.requested_by:
        p.error("--requested-by is required for 'mutate'")
    if args.command in ("query", "get", "mutate") and not args.session_id:
        # No --session-id passed (no open_session() call preceded this CLI
        # invocation) — generate a fallback now rather than relying solely
        # on _session_or_fallback() deep inside audit logging, so a
        # --debug query and a mutate in the same shell session share the
        # visible-to-the-caller id shape consistently.
        args.session_id = _session_or_fallback(None)

    try:
        if args.command == "health":
            print(json.dumps(health_check(args.tag), indent=2))
        elif args.command == "list-envs":
            print(json.dumps({"configured_tags": list_configured_tags()}, indent=2))
        elif args.command == "query":
            filters = json.loads(args.filters) if args.filters else None
            fields = json.loads(args.fields) if args.fields else None
            print(json.dumps(query_resource(args.tag, args.doctype, filters, fields, args.limit,
                                             debug=args.debug, session_id=args.session_id,
                                             persona_code=args.persona_code,
                                             requested_by=args.requested_by), indent=2))
        elif args.command == "get":
            print(json.dumps(get_resource(args.tag, args.doctype, args.name, not args.no_strip,
                                           debug=args.debug, session_id=args.session_id,
                                           persona_code=args.persona_code,
                                           requested_by=args.requested_by), indent=2))
        elif args.command == "bin-qty":
            print(json.dumps(get_bin_qty(args.tag, args.item_code, args.warehouse), indent=2))
        elif args.command == "recon-items":
            print(json.dumps(
                get_stock_reconciliation_items(args.tag, args.warehouse, args.company,
                                                args.posting_date, args.item_code, args.posting_time),
                indent=2,
            ))
        elif args.command == "mutate":
            if args.payload_file:
                with open(args.payload_file, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            elif args.payload:
                payload = json.loads(args.payload)
            else:
                payload = None
            print(json.dumps(
                mutate_resource(args.tag, args.doctype, args.action, payload, args.name,
                                 args.mode, args.requested_by,
                                 session_id=args.session_id, persona_code=args.persona_code,
                                 user_approved=args.user_approved, approval_note=args.approval_note),
                indent=2,
            ))
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
