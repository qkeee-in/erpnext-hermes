#!/usr/bin/env python3
"""
qkeee-erp-fixed-asset-manager connector — read+write copy of the
canonical qkeee-erp-frappe-core ERPNext (Frappe REST API) client, per the
self-contained-copies architecture decision. This persona is
read-write-capable (gated by qkeee_erp.mode) for the full asset
lifecycle. Depreciation runs and disposals additionally require a
double-confirm step enforced in scripts/render_depreciation_run.py and
scripts/render_disposal.py — not in this file — since those are
financially irreversible-in-spirit even though technically cancelable.

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
This is enforced in write_resource()/mutate below, not just in the
calling skill's prompt.

Audit-trail retrofit (synced from qkeee-erp-frappe-core): every
write through mutate_resource() is additionally logged to Qkeee Bot
Audit Log (two-phase, best-effort — see qkeee-erp-bot-init/references/
bot-doctypes-design.md). Known gap: call_whitelisted_method() below
(make_depreciation_entry / scrap_asset / restore_asset /
make_sales_invoice) bypasses mutate_resource() entirely — these RPC-
style writes are NOT yet audit-logged. Flagged in
references/connector-reference.md; not fixed in this pass.

Session/Message logging (synced from qkeee-erp-frappe-core): opt-in per
caller via open_session()/log_message()/close_session(), best-effort,
normally gated on qkeee_erp.debug at the SKILL.md level. Persona
registration (ensure_persona_registered()) is a best-effort,
unconditional (not debug-gated) idempotent upsert of this persona's
Qkeee Bot Persona master-data row.
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
SKILL_LABEL = "qkeee-erp-fixed-asset-manager"

from confirm_token import depreciation_run_token, disposal_token, is_fresh

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

    Refuses a non-https base_url by default — _request() sends the bot
    account's api_key/api_secret in a plain Authorization header on every
    call, so a plaintext http:// target means those credentials cross the
    wire in the clear. Set QKEEE_ERP_<TAG>_ALLOW_INSECURE=1 to override
    for a genuine local/dev http instance.

    Also resolves two OPTIONAL per-tag values — QKEEE_ERP_<TAG>_DEBUG and
    QKEEE_ERP_<TAG>_REQUESTED_BY — as `debug_default`/`requested_by_default`
    on the returned dict. Unlike BASE_URL/API_KEY/API_SECRET these are
    never required and never raise if absent (default False / ""). Moved
    here from `metadata.hermes.config` (was a single global
    qkeee_erp.debug/qkeee_erp.requested_by value shared across every tag
    in a profile) specifically so switching `--tag` also switches these —
    a profile juggling `hrms-demo` and `prod` can have debug on for one
    and off for the other, and a different requester identity per
    environment, without a global toggle bleeding across both. CLI
    callers pass `--debug`/`--requested-by` as a per-invocation override
    on top of this default; they never replace it as the source of truth.
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
            f"Set them in this agent profile's own .env file, then retry."
        )

    base_url = base_url.rstrip("/")
    if not base_url.startswith("https://") and not os.environ.get(_tag_env_var(tag, "ALLOW_INSECURE")):
        raise ConnectorError(
            f"'{_tag_env_var(tag, 'BASE_URL')}' ({base_url}) is not https — refusing to send "
            f"credentials over plaintext transport by default. Set "
            f"{_tag_env_var(tag, 'ALLOW_INSECURE')}=1 to override for a genuine local/dev "
            f"http instance."
        )

    return {
        "tag": tag,
        "base_url": base_url,
        "api_key": api_key,
        "api_secret": api_secret,
        "debug_default": _parse_bool_env(os.environ.get(_tag_env_var(tag, "DEBUG"))),
        "requested_by_default": os.environ.get(_tag_env_var(tag, "REQUESTED_BY"), ""),
    }


def _request(cfg: dict, method: str, path: str, params: dict = None, payload: dict = None) -> dict:
    url = cfg["base_url"] + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {cfg['api_key']}:{cfg['api_secret']}")
    req.add_header("Content-Type", "application/json")
    # Python's default urllib UA ("Python-urllib/x.y") is blocked by common
    # WAF/bot-protection (e.g. Cloudflare) fronting production ERPNext
    # instances, returning a 403 that looks like an auth failure but isn't
    # — confirmed against <erp-instance>, where curl succeeded and unmodified
    # urllib got blocked on UA alone. Always send an explicit UA.
    req.add_header("User-Agent", "qkeee-erp-frappe-core/1.0")

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
    permission on any specific DocType (e.g. a role-restricted bot account
    may health-check fine yet still 403 on a later read/write against a
    doctype it lacks access to). Report a later permission error as its
    own distinct failure mode, not folded into "connectivity is broken".
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

    `debug=True` additionally logs this read to Qkeee Bot Audit Log (best-
    effort). Read logging is debug-gated, not unconditional like writes —
    a read-heavy persona (e.g. MIS Analyst) can generate far more Read
    calls than any other action type, so logging every read unconditionally
    would have made Audit Log itself the volume/bloat problem the debug
    gate exists to avoid. See bot-doctypes-design.md decision 10.
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


# Fields stripped from get_resource() output: audit/system metadata and
# presentation-only HTML/display fields that no review or reporting logic
# in this skill reads. Never strips Link fields, child tables, or anything
# review steps check for validity. Measured live against <erp-instance>
# (Sales Order doc, same field shapes recur across ERPNext doctypes):
# ~38% byte reduction. Deliberately keeps `modified` (unlike the other
# qkeee-erp-* copies) — this skill's mutate_resource() submit path takes
# an optional expected_modified for its TOCTOU check, sourced from
# whatever value a caller last read off the record; stripping it here
# would silently break that if a review step ever reads it via get_resource().
_NOISE_FIELDS = {
    "owner", "creation", "modified_by", "idx", "naming_series",
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
    """Single-resource full-doc GET — the only way to get child-table rows.

    Confirmed live against <erp-instance>: Frappe's list endpoint
    (query_resource()) silently drops Table-type (child-table) fields even
    when named in `fields`, while the single-resource GET ignores `fields`
    entirely and always returns the full doc (~94 top-level keys on a
    Sales Order). Use get_resource() only when child-table Link validity
    actually needs checking (e.g. a review-before-submit step) — for
    reads that don't need child-table data (status checks, report reads),
    query_resource() with filters+fields is ~25x cheaper.

    strip_noise=True (default) drops audit/system metadata and
    presentation-only HTML fields before returning — see _NOISE_FIELDS.

    `debug=True` logs this read to Qkeee Bot Audit Log, same as
    query_resource() — see that function's docstring for why this is
    debug-gated rather than unconditional.
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


def resource_exists(tag: str, doctype: str, name: str) -> bool:
    """404-tolerant existence check (e.g. "has bot-init provisioned the
    audit doctypes on this instance yet"). Never logged, never gated."""
    try:
        get_resource(tag, doctype, name, strip_noise=False)
        return True
    except ConnectorError as e:
        if "(404)" in str(e):
            return False
        raise


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
# Audit logging (Qkeee Bot Audit Log) — synced from qkeee-erp-frappe-core.
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
    """Field-by-field diff for the Update action's field_diff JSON. Compares
    top-level keys only (child-table diffing isn't attempted — a child
    table's own rows would need their own before/after comparison, out of
    scope for this connector-level helper); skips noise/metadata fields."""
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
    """Raw best-effort insert into Qkeee Bot Audit Log. Returns the created
    record's name, or None on any failure (doctype not provisioned,
    permission denied, network error, etc.) — never raises."""
    try:
        payload = {"doctype": AUDIT_LOG_DOCTYPE, **fields}
        result = _request(cfg, "POST", f"/api/resource/{urllib.parse.quote(AUDIT_LOG_DOCTYPE)}", payload=payload)
        return (result.get("data") or {}).get("name")
    except Exception as e:
        # Broad by design: audit logging must never surface a failure mode
        # that could be mistaken for the real write failing. Still warn to
        # stderr so a persistently-failing audit path (e.g. a mandatory
        # field validation error) is visible in logs instead of just an
        # empty Audit Log table with no trace of why.
        print(f"WARN: audit log insert failed (non-fatal): {e}", file=sys.stderr)
        return None


def _audit_update(cfg: dict, log_name: str, fields: dict) -> bool:
    """Raw best-effort update of an existing Audit Log row. Returns success."""
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
    """Best-effort submit (docstatus lock) of a finished Audit Log row.
    Failure here (e.g. the row's own mandatory fields didn't validate)
    leaves the row as a readable draft rather than blocking anything —
    the row's content is what matters for the audit trail; submission is
    a tamper-evidence nicety on top."""
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
    """Best-effort insert+submit Audit Log row for a debug-mode read.
    Insert/update are collapsed into one status ("Success") since a read
    has no in-flight state to crash into, but submit still runs so the
    row doesn't sit as an unsubmitted Draft like two-phase write rows
    would if left unfinished."""
    if doctype in AUDIT_EXEMPT_DOCTYPES:
        return
    log_name = _audit_insert(cfg, {
        "session": _session_or_fallback(session_id),
        "persona_code": persona_code or "",
        "environment_tag": cfg.get("tag", ""),
        "action": "Read",
        "reference_doctype": doctype,
        "reference_name": name or "",
        "requested_by": requested_by or "",
        "timestamp": _now_iso(),
        "status": "Success",
        "user_approved": "Not Required",
    })
    _audit_submit(cfg, log_name)


def record_audit_log_start(cfg: dict, *, action: str, doctype: str, name: str, requested_by: str,
                            session_id: str = None, persona_code: str = None,
                            payload_before: dict = None, user_approved: bool = False,
                            approval_note: str = None) -> str:
    """Phase 1 of two-phase audit logging: insert an `Attempted` row
    BEFORE the real ERPNext write happens. If the process crashes between
    this call and record_audit_log_finish(), the orphaned `Attempted` row
    is the detectable trace of an unfinished/unknown-outcome write — see
    bot-doctypes-design.md decision 7. Returns the row's name, or None if
    the insert itself failed (doctype not provisioned, etc.) — callers
    must treat None as "logging unavailable, proceed anyway", never as a
    reason to abort the real write.
    """
    if doctype in AUDIT_EXEMPT_DOCTYPES:
        return None
    return _audit_insert(cfg, {
        "session": _session_or_fallback(session_id),
        "persona_code": persona_code or "",
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
    """Phase 2: flip an `Attempted` row to `Success`/`Failure` after the
    real write completes (or fails). Computes field_diff from
    payload_before/payload_after when both are present (Update only —
    Create has nothing to diff against). Best-effort; failures here are
    swallowed, same rationale as everywhere else in this section."""
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
        fields["error_detail"] = error_detail[:1900]  # Small Text-ish headroom
    if audit_comment_posted is not None:
        fields["audit_comment_posted"] = 1 if audit_comment_posted else 0
    if _audit_update(cfg, log_name, fields):
        _audit_submit(cfg, log_name)


# --------------------------------------------------------------------------
# Session / Message logging — debug-mode only, opt-in per caller.
#
# Unlike Audit Log (always attempted for writes), Session/Message rows are
# only ever created when the calling skill explicitly opts in — normally
# gated on qkeee_erp.debug at the SKILL.md level. This module doesn't
# enforce that gate itself; it's the caller's job to only call these when
# qkeee_erp.debug is true.
# --------------------------------------------------------------------------

def open_session(tag: str, *, user: str, persona_code: str, mode: str, debug_mode: bool = True) -> str:
    """Create a Qkeee Bot Session row. Returns the session id (the row's
    `name`) on success, or a locally-generated fallback id if the insert
    failed — callers always get a usable session_id string to thread
    through subsequent calls (Audit Log's `session` field is Data, not a
    Link, precisely so it can carry this fallback id — see
    bot-doctypes-design.md decision 10)."""
    cfg = get_env_config(tag)
    name = _audit_insert_generic(cfg, SESSION_DOCTYPE, {
        "user": user,
        "persona": persona_code,
        "environment_tag": tag,
        "mode": "Read Write" if mode == "read-write" else "Read Only",
        "debug_mode": 1 if debug_mode else 0,
        "started_on": _now_iso(),
        "status": "Active",
    })
    return name or f"local-{_now_iso()}"


def close_session(tag: str, session_id: str, *, status: str = "Closed") -> None:
    """Best-effort: mark a Session row Closed/Error. No-op if session_id
    is a local fallback id (never persisted) or the update fails."""
    if not session_id or session_id.startswith("local-"):
        return
    cfg = get_env_config(tag)
    try:
        path = f"/api/resource/{urllib.parse.quote(SESSION_DOCTYPE)}/{urllib.parse.quote(session_id)}"
        _request(cfg, "PUT", path, payload={"status": status, "ended_on": _now_iso()})
    except ConnectorError:
        pass


def log_message(tag: str, *, session_id: str, speaker: str, content: str,
                 related_capability: str = None, in_reply_to: str = None) -> str:
    """Best-effort insert of a Qkeee Bot Message row. Returns the row's
    name (usable as a future in_reply_to target), or None on failure."""
    cfg = get_env_config(tag)
    return _audit_insert_generic(cfg, MESSAGE_DOCTYPE, {
        "session": session_id,
        "speaker": speaker,
        "content": content,
        "related_capability": related_capability,
        "in_reply_to": in_reply_to,
    })


def _audit_insert_generic(cfg: dict, doctype: str, fields: dict) -> str:
    """Same shape as _audit_insert() but for Session/Message rather than
    Audit Log specifically — kept separate since Session/Message never
    go through the two-phase Attempted/Success lifecycle Audit Log does."""
    try:
        payload = {"doctype": doctype, **{k: v for k, v in fields.items() if v is not None}}
        result = _request(cfg, "POST", f"/api/resource/{urllib.parse.quote(doctype)}", payload=payload)
        return (result.get("data") or {}).get("name")
    except ConnectorError as e:
        # Was silently returning None here — a failed Session/Message insert
        # was indistinguishable from success to the caller (CLI still exits
        # 0, just prints message_id/session_id: null). Warn to stderr, same
        # as _audit_insert() does for Audit Log, so a persistently-failing
        # doctype (not provisioned, missing Qkeee Bot role permission, a
        # mandatory field rejected) is visible instead of invisible.
        print(f"WARN: {doctype} insert failed (non-fatal): {e}", file=sys.stderr)
        return None


def ensure_persona_registered(tag: str, *, persona_code: str, persona_label: str,
                               default_mode: str = "read-only", non_negotiables: str = None) -> str:
    """Best-effort idempotent upsert of this persona's Qkeee Bot Persona row.
    Unconditional — NOT debug-gated, not a log (master data, see
    bot-doctypes-design.md's Persona section). Never raises, never blocks
    the caller — but unlike the pure-logging helpers above, this returns a
    status string instead of swallowing the outcome entirely, because a
    caller silently getting no signal here is exactly how this went
    invisible in practice: the doctype not being provisioned yet (bot-init
    not run on this instance) and a genuinely successful no-op ("already
    registered") were indistinguishable from the outside, so nothing ever
    told the calling skill (or the user) that registration wasn't landing.

    Returns "already_registered" | "created" | "failed". A caller that
    gets "failed" should treat it as the same signal as a `logged_in_as`
    that looks like a personal account — worth proactively surfacing and
    suggesting `qkeee-erp-bot-init`, not silently ignoring."""
    cfg = get_env_config(tag)
    if resource_exists(tag, PERSONA_DOCTYPE, persona_code):
        return "already_registered"
    try:
        _request(cfg, "POST", f"/api/resource/{urllib.parse.quote(PERSONA_DOCTYPE)}", payload={
            "doctype": PERSONA_DOCTYPE,
            "persona_code": persona_code,
            "persona_label": persona_label,
            "default_mode": "Read Write" if default_mode == "read-write" else "Read Only",
            "non_negotiables": non_negotiables or "",
        })
        return "created"
    except ConnectorError as e:
        print(f"WARN: persona registration failed (non-fatal): {e}", file=sys.stderr)
        return "failed"


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------

def mutate_resource(tag: str, doctype: str, action: str, payload: dict = None,
                     name: str = None, mode: str = "read-only", requested_by: str = None,
                     skip_comment: bool = False,
                     *, session_id: str = None, persona_code: str = None,
                     user_approved: bool = False, approval_note: str = None) -> dict:
    """Generic resource mutate — create/update/submit/cancel a DocType record.

    `mode` must be passed explicitly by the caller (sourced from
    metadata.hermes.config qkeee_erp.mode) — this function refuses to
    guess a safe default and refuses to write unless mode == "read-write".

    `requested_by` (the ERPNext user id/email of the human who asked for
    this change, sourced per-tag from QKEEE_ERP_<TAG>_REQUESTED_BY, with
    a CLI --requested-by as a per-call override) is required for every
    write — the connector authenticates as a shared bot account, so
    without this the ERPNext audit trail would show only the bot, never
    who actually asked. On success, a best-effort Comment naming the
    requester is posted to the affected record (see record_comment()).

    `skip_comment=True` suppresses that default Comment — for a caller
    that's about to post its own, more specific attribution comment
    right after this call returns (e.g. a capability-specific wrapper
    that names the actual action taken, not just "created"/"updated").
    Qkeee Bot Audit Log logging is unaffected either way; only the
    ERPNext-side Comment is skipped.

    `user_approved` should be True only when the caller actually ran this
    write's confirm stage with the user first — it's logged to Qkeee Bot
    Audit Log's `user_approved` field for later scanning, not enforced as
    a gate here (see record_audit_log_start()'s docstring and
    bot-doctypes-design.md decision 14). Defaults to False deliberately:
    a caller that forgets to pass it shows up as "Not Confirmed" on scan,
    which is the intended detection behavior, not a silent default.
    """
    if mode != "read-write":
        raise ReadOnlyModeError(
            f"Refusing {action} on '{doctype}': qkeee_erp.mode is '{mode}', not 'read-write'. "
            f"Switch modes explicitly if this write is intended."
        )
    if not requested_by:
        raise MissingRequesterError(
            f"Refusing {action} on '{doctype}': requested_by is missing. "
            f"Set {_tag_env_var(tag, 'REQUESTED_BY')} in this profile's .env (per-tag default), "
            f"or pass --requested-by for this call only."
        )

    cfg = get_env_config(tag)

    # Pre-image for Update's field_diff — an extra GET, only when this
    # doctype is actually audited (skip for Session/Message/etc, which
    # never reach mutate_resource() as a target doctype in practice, and
    # skip when the doctype isn't in AUDIT_EXEMPT_DOCTYPES but the target
    # simply doesn't need diffing, e.g. Create has no "before").
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
        result = _do_mutate(cfg, doctype, action, payload, name, requested_by, skip_comment=skip_comment)
    except ConnectorError as e:
        record_audit_log_finish(cfg, audit_log_name, status="Failure", error_detail=str(e))
        raise

    # Success path: extract whatever's usable as payload_after / the
    # audit-comment outcome to close out the Attempted row.
    data = result.get("data") if isinstance(result, dict) else None
    if data is None and isinstance(result, dict):
        data = result.get("message")  # submit/cancel return {"message": {...}} instead of {"data": {...}}
    reference_name = (data or {}).get("name") if isinstance(data, dict) else name
    audit_comment_posted = result.pop("_audit_comment_posted", None) if isinstance(result, dict) else None
    record_audit_log_finish(
        cfg, audit_log_name, status="Success", reference_name=reference_name,
        payload_before=payload_before, payload_after=data if isinstance(data, dict) else None,
        audit_comment_posted=audit_comment_posted,
    )
    return result


def _do_mutate(cfg: dict, doctype: str, action: str, payload: dict, name: str, requested_by: str,
                skip_comment: bool = False) -> dict:
    """The actual per-action HTTP dispatch, unchanged from before the
    audit-logging retrofit — factored out so mutate_resource() can wrap it
    uniformly with the two-phase Attempted/Success/Failure logging above
    without duplicating this logic per action.

    `skip_comment` suppresses the default `record_comment()` call per
    action below — see mutate_resource()'s docstring."""
    if action == "create":
        path = f"/api/resource/{urllib.parse.quote(doctype)}"
        result = _request(cfg, "POST", path, payload=payload)
        created_name = (result.get("data") or {}).get("name")
        comment_posted = None
        if created_name and not skip_comment:
            comment_posted = record_comment(
                cfg, doctype, created_name,
                f"[{SKILL_LABEL}] created — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        result["_audit_comment_posted"] = comment_posted
        return result
    if action == "update":
        if not name:
            raise ConnectorError("update requires a record 'name'.")
        path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
        result = _request(cfg, "PUT", path, payload=payload)
        comment_posted = None
        if not skip_comment:
            comment_posted = record_comment(
                cfg, doctype, name,
                f"[{SKILL_LABEL}] updated — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        result["_audit_comment_posted"] = comment_posted
        return result
    if action == "submit":
        if not name:
            raise ConnectorError("submit requires a record 'name'.")
        # frappe.client.submit builds its doc via frappe.get_doc(dict) — a
        # sparse {doctype, name} payload has no DB-loaded field values, so
        # validate() fails mandatory-field checks. Fetch the full record
        # first, then submit that. This necessarily reposts every stored
        # field verbatim (submit is not a diff) — including any PII fields
        # already present on the record, regardless of what the calling
        # skill's current task scope is. That's expected: submit locks in
        # the record as-is, it doesn't grant new write access to fields the
        # caller didn't set. A skill's own PII-scope discipline governs
        # what it *writes new values to* via create/update, not what a
        # submit call necessarily echoes back to lock the doc.
        get_path = f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name)}"
        existing = _request(cfg, "GET", get_path)
        full_doc = existing.get("data")
        if not full_doc:
            raise ConnectorError(f"Could not load '{doctype}' '{name}' before submit — nothing to submit.")
        result = _request(cfg, "POST", "/api/method/frappe.client.submit", payload={"doc": full_doc})
        comment_posted = None
        if not skip_comment:
            comment_posted = record_comment(
                cfg, doctype, name,
                f"[{SKILL_LABEL}] submitted — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        result["_audit_comment_posted"] = comment_posted
        return result
    if action == "cancel":
        if not name:
            raise ConnectorError("cancel requires a record 'name'.")
        body = {"doctype": doctype, "name": name}
        result = _request(cfg, "POST", "/api/method/frappe.client.cancel", payload=body)
        comment_posted = None
        if not skip_comment:
            comment_posted = record_comment(
                cfg, doctype, name,
                f"[{SKILL_LABEL}] cancelled — requested by {requested_by}, applied via qkeee-erp bot.",
            )
        result["_audit_comment_posted"] = comment_posted
        return result
    if action == "delete":
        if not name:
            raise ConnectorError("delete requires a record 'name'.")
        # Post the audit comment before deleting — once the record is gone
        # there's nothing left in ERPNext to attach a Comment to.
        comment_posted = None
        if not skip_comment:
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


def mutate_resource_with_concurrency(tag: str, doctype: str, action: str, payload: dict = None,
                                      name: str = None, mode: str = "read-only",
                                      expected_modified: str = None, requested_by: str = None,
                                      skip_comment: bool = False,
                                      *, session_id: str = None, persona_code: str = None,
                                      user_approved: bool = False, approval_note: str = None) -> dict:
    """TOCTOU-checked wrapper around the shared `mutate_resource()` — this
    skill's own extension for the submit-time concurrency check described
    in SKILL.md (Asset capitalization / depreciation-run / disposal
    review steps). Deliberately kept OUT of `SHARED_FUNCTIONS` in
    `sync_to_personas.py` by using a name `mutate_resource()` doesn't
    have: a prior version of this file defined `expected_modified` as a
    parameter directly on `mutate_resource()` itself, which meant every
    `qkeee-erp-frappe-core` sync silently overwrote it with core's canonical
    signature (which has no such param) — the loss went unnoticed because
    nothing tested the CLI's `mutate` dispatch end-to-end. Restored
    2026-08-18 as a wrapper specifically so this can never happen again:
    `mutate_resource_with_concurrency` doesn't collide with any core
    function name, so `merge_py()`'s by-name matching leaves it alone.

    `expected_modified` (submit only): if the caller knows the record's
    `modified` timestamp from when it last read/staged the draft, pass it
    here. Before delegating to `mutate_resource()`'s submit path, this
    re-fetches the record and refuses (raising `ConnectorError`, not
    silently proceeding) if `modified` has moved on since — someone else
    edited the record between staging and submit. This narrows (does not
    eliminate) the fetch-then-submit TOCTOU gap; the remaining
    unmitigated window between this check and the submit POST itself is
    what `frappe.client.submit`'s own TimestampMismatchError backstops.

    Every other action, and `submit` with `expected_modified` omitted,
    passes straight through to `mutate_resource()` unchanged — this
    wrapper adds a pre-check, it never re-implements the write itself.
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
                            skip_comment=skip_comment, session_id=session_id, persona_code=persona_code,
                            user_approved=user_approved, approval_note=approval_note)


WHITELISTED_METHODS = {
    "make_depreciation_entry": "/api/method/erpnext.assets.doctype.asset.depreciation.make_depreciation_entry",
    "scrap_asset": "/api/method/erpnext.assets.doctype.asset.depreciation.scrap_asset",
    "restore_asset": "/api/method/erpnext.assets.doctype.asset.depreciation.restore_asset",
    "make_sales_invoice": "/api/method/erpnext.assets.doctype.asset.asset.make_sales_invoice",
}

# These three carry the skill's double-confirm non-negotiable (depreciation
# runs and disposals) — restore_asset is a recovery action, not a
# write-off/posting action, so it isn't token-gated.
TOKEN_REQUIRED_METHODS = {"make_depreciation_entry", "scrap_asset", "make_sales_invoice"}


def call_whitelisted_method(tag: str, method: str, body: dict, mode: str = "read-only",
                             confirmation_token: str = None, token_facts: dict = None,
                             requested_by: str = None) -> dict:
    """Call one of the four skill-specific whitelisted RPC methods.

    `body` is sent to ERPNext verbatim as the RPC's actual arguments —
    only the exact fields that method's real signature accepts (see
    references/connector-reference.md's Endpoints table), nothing more.

    These bypass mutate_resource()'s generic create/update/submit/cancel
    action set (they don't fit it), which previously meant they also
    bypassed its code-level `mode == "read-write"` gate — that gate was
    enforced only by a prose instruction at the calling skill's prompt
    level. This function is the single call path for all four methods
    and enforces the same gate in code, exactly like mutate_resource().
    It also enforces the same `requested_by` gate — the connector
    authenticates as a shared bot account, so every write here still
    needs to know which human asked, same as mutate_resource().

    For the three double-confirm methods (make_depreciation_entry,
    scrap_asset, make_sales_invoice), `confirmation_token` is also
    required and must match the token the corresponding render script
    (render_depreciation_run.py / render_disposal.py) computed from the
    same financial facts — see confirm_token.py. This ties the render
    step to the execute step in code: a caller cannot reach the RPC call
    without having rendered a matching confirmation first.

    `token_facts` carries the identifying facts needed to recompute that
    token (asset, schedule/method, date, amount) — deliberately kept
    separate from `body` so verification-only facts never leak into the
    actual API payload sent to ERPNext.

    On success, posts a best-effort audit Comment onto the relevant
    Asset record (`body["asset_name"]`, the field every one of these
    four RPCs takes) naming the requester — same shape as
    mutate_resource()'s. A comment failure never blocks the RPC result.

    KNOWN GAP: unlike mutate_resource(), this function does
    NOT yet log to Qkeee Bot Audit Log — it bypasses mutate_resource()
    entirely (RPC call shape, not create/update/submit/cancel), so the
    audit-logging retrofit wired into mutate_resource() doesn't cover it.
    Depreciation runs, scraps, and disposals via this path are currently
    unaudited in Qkeee Bot Audit Log even though they post the usual
    ERPNext Comment. Flagged in references/connector-reference.md; not
    fixed in this pass — closing it would mean either duplicating the
    two-phase logging here or refactoring this function to route through
    a shared write-wrapper, both deferred as follow-up work.
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
                f"output (render_depreciation_run.py / render_disposal.py) first and pass "
                f"its exact token here. This method cannot be called without one."
            )
        issued_at = (token_facts or {}).get("issued_at")
        if not issued_at:
            raise ConnectorError(
                f"'{method}' requires token_facts['issued_at'] — the render script now "
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
            raise ConnectorError(
                f"confirmation_token for '{method}' has expired (older than 15 minutes) — "
                f"re-render the confirmation against current data before executing. This "
                f"guards against acting on a stale render, e.g. an asset revalued or a "
                f"schedule amended since the token was issued."
            )

    cfg = get_env_config(tag)
    result = _request(cfg, "POST", WHITELISTED_METHODS[method], payload=body)
    asset_name = (body or {}).get("asset_name")
    if asset_name:
        record_comment(
            cfg, "Asset", asset_name,
            f"[{SKILL_LABEL}] {method} — requested by {requested_by}, applied via qkeee-erp bot.",
        )
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


def list_configured_tags() -> list:
    """List environment tags with a full var set (BASE_URL+API_KEY+API_SECRET)
    already present in os.environ. Enables the 'list environment tags'
    part of the environment-configuration capability."""
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


def discover_harness_http_tool() -> dict:
    """Harness capability discovery stub — persona/host code should check for a
    harness-native HTTP-capable tool before shelling out to this script.
    Returns a map describing what this script assumes (nothing pre-discovered)."""
    return {"harness_http_tool_detected": False, "fallback": "urllib (this script)"}


def _cli():
    p = argparse.ArgumentParser(description="qkeee-erp-fixed-asset-manager connector CLI")
    # No env-var fallback for --tag/--mode: these must come from the caller
    # (resolved from metadata.hermes.config qkeee_erp.active_env / .mode),
    # never picked up ambiently from an unrelated shell var — that would
    # bypass the read-only gate silently. Required only where the command
    # actually needs them (list-envs needs neither).
    p.add_argument("--tag", help="environment tag, from qkeee_erp.active_env (required for health/query/mutate)")
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
    q.add_argument("--filters", help="JSON list, e.g. '[[\"status\",\"=\",\"Open\"]]'")
    q.add_argument("--fields", help="JSON list, e.g. '[\"name\",\"status\"]'")
    q.add_argument("--limit", type=int, default=20)

    g = sub.add_parser("get", help="Single-resource full-doc GET (includes child tables) — noise-stripped by default")
    g.add_argument("doctype")
    g.add_argument("name")
    g.add_argument("--no-strip", action="store_true", help="skip noise-stripping, return the raw doc verbatim")

    m = sub.add_parser("mutate")
    m.add_argument("doctype")
    m.add_argument("action", choices=["create", "update", "submit", "cancel", "delete"])
    m.add_argument("--payload", help="JSON object for create/update")
    m.add_argument("--name", help="record name, required for update/submit/cancel/delete")
    m.add_argument("--expected-modified", help="submit only — TOCTOU check against the record's last-read 'modified' timestamp")

    rp = sub.add_parser("register-persona", help="Idempotent upsert of this persona's Qkeee Bot Persona row (master data, unconditional)")
    rp.add_argument("--persona-code", required=True, help="e.g. qkeee-erp-fixed-asset-manager")
    rp.add_argument("--persona-label", required=True, help="display name, e.g. 'Fixed Asset Manager'")
    rp.add_argument("--default-mode", choices=["read-only", "read-write"], default="read-only",
                     help="this persona's default qkeee_erp.mode")
    rp.add_argument("--non-negotiables", help="free text copied from the persona's SKILL.md, informational only")

    os_ = sub.add_parser("open-session", help="Create a Qkeee Bot Session row (debug-mode logging)")
    os_.add_argument("--user", help="ERPNext user id/email this session acts on behalf of")
    os_.add_argument("--persona-code", required=True, help="e.g. qkeee-erp-fixed-asset-manager")
    os_.add_argument("--mode", required=True, choices=["read-only", "read-write"],
                      help="from qkeee_erp.mode at session start")
    os_.add_argument("--no-debug", action="store_true", help="mark debug_mode=False on the Session row (default True)")

    lm = sub.add_parser("log-message", help="Insert a Qkeee Bot Message row (debug-mode logging)")
    lm.add_argument("--session-id", required=True)
    lm.add_argument("--speaker", required=True,
                     choices=["User", "Bot Analysis", "Bot Response", "Bot Action", "System"])
    lm.add_argument("--content", required=True)
    lm.add_argument("--related-capability", help="e.g. 'Depreciation run drafting'")
    lm.add_argument("--in-reply-to", help="name of the Qkeee Bot Message this turn answers")

    cs = sub.add_parser("close-session", help="Mark a Qkeee Bot Session row Closed/Error")
    cs.add_argument("--session-id", required=True)
    cs.add_argument("--status", choices=["Closed", "Error"], default="Closed")

    args = p.parse_args()

    if args.command in ("health", "query", "get", "mutate",
                         "register-persona", "open-session", "log-message", "close-session") and not args.tag:
        p.error(f"--tag is required for '{args.command}'")
    if args.command == "mutate" and not args.mode:
        p.error("--mode is required for 'mutate'")
    if args.command in ("query", "get", "mutate") and not args.session_id:
        # No --session-id passed (no open_session() call preceded this CLI
        # invocation) — generate a fallback now rather than relying solely
        # on _session_or_fallback() deep inside audit logging, so a
        # --debug query and a mutate in the same shell session share the
        # visible-to-the-caller id shape consistently.
        args.session_id = _session_or_fallback(None)

    # debug/requested-by default from the active TAG's own env vars
    # (QKEEE_ERP_<TAG>_DEBUG / _REQUESTED_BY) — per-tag, not a single
    # global qkeee_erp.debug/.requested_by. --debug/--requested-by on the
    # CLI are a per-call override on top of that default, never a
    # replacement for it. Swallow a resolution failure here — a genuinely
    # missing/misconfigured tag surfaces its own specific error from the
    # real call below.
    tag_debug_default, tag_requested_by_default = False, ""
    if args.command in ("query", "get", "report", "mutate", "open-session"):
        try:
            _tag_cfg = get_env_config(args.tag)
            tag_debug_default = _tag_cfg["debug_default"]
            tag_requested_by_default = _tag_cfg["requested_by_default"]
        except ConnectorError:
            pass
    effective_debug = args.debug or tag_debug_default
    effective_requested_by = args.requested_by or tag_requested_by_default

    if args.command == "mutate" and not effective_requested_by:
        p.error(
            "--requested-by is required for 'mutate' (or set "
            f"{_tag_env_var(args.tag, 'REQUESTED_BY')} in this profile's .env)"
        )
    if args.command == "open-session" and not effective_requested_by:
        p.error(
            "--user is required for 'open-session' (or set "
            f"{_tag_env_var(args.tag, 'REQUESTED_BY')} in this profile's .env)"
        )

    try:
        if args.command == "health":
            print(json.dumps(health_check(args.tag), indent=2))
        elif args.command == "list-envs":
            print(json.dumps({"configured_tags": list_configured_tags()}, indent=2))
        elif args.command == "query":
            filters = json.loads(args.filters) if args.filters else None
            fields = json.loads(args.fields) if args.fields else None
            print(json.dumps(query_resource(args.tag, args.doctype, filters, fields, args.limit,
                                             debug=effective_debug, session_id=args.session_id,
                                             persona_code=args.persona_code,
                                             requested_by=effective_requested_by), indent=2))
        elif args.command == "get":
            print(json.dumps(get_resource(args.tag, args.doctype, args.name, not args.no_strip,
                                           debug=effective_debug, session_id=args.session_id,
                                           persona_code=args.persona_code,
                                           requested_by=effective_requested_by), indent=2))
        elif args.command == "mutate":
            payload = json.loads(args.payload) if args.payload else None
            print(json.dumps(
                mutate_resource_with_concurrency(
                    args.tag, args.doctype, args.action, payload, args.name,
                    mode=args.mode, expected_modified=args.expected_modified,
                    requested_by=effective_requested_by,
                    session_id=args.session_id, persona_code=args.persona_code,
                    user_approved=args.user_approved, approval_note=args.approval_note),
                indent=2,
            ))
        elif args.command == "register-persona":
            status = ensure_persona_registered(args.tag, persona_code=args.persona_code,
                                                persona_label=args.persona_label,
                                                default_mode=args.default_mode,
                                                non_negotiables=args.non_negotiables)
            print(json.dumps({"ok": True, "status": status}, indent=2))
        elif args.command == "open-session":
            session_id = open_session(args.tag, user=effective_requested_by, persona_code=args.persona_code,
                                       mode=args.mode, debug_mode=not args.no_debug)
            print(json.dumps({"session_id": session_id}, indent=2))
        elif args.command == "log-message":
            message_id = log_message(args.tag, session_id=args.session_id, speaker=args.speaker,
                                      content=args.content, related_capability=args.related_capability,
                                      in_reply_to=args.in_reply_to)
            print(json.dumps({"message_id": message_id}, indent=2))
        elif args.command == "close-session":
            close_session(args.tag, args.session_id, status=args.status)
            print(json.dumps({"ok": True}, indent=2))
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)




def run_query_report(tag: str, report_name: str, filters: dict = None,
                      *, debug: bool = False, session_id: str = None, persona_code: str = None,
                      requested_by: str = None) -> dict:
    """Run one of ERPNext's own built-in reports server-side (Query Report
    or Script Report) via frappe.desk.query_report.run, instead of hand-
    aggregating raw transactional rows into the same shape. Prefer this
    whenever a built-in report covers the need — report logic already
    implements dimension filters, Finance Book gates, and currency
    conversion correctly; a hand-rolled aggregation risks silently
    missing one of those. Read-only in effect (runs a report, creates
    nothing).

    GET + query-string filters, not POST — confirmed live ("Sales Order
    Analysis" against a real ERPNext v15 instance, real per-line data
    returned). `filters` is a plain dict of report-specific filter values;
    field names vary per report — confirm the exact filter keys a given
    report expects by opening it in the ERPNext UI once, since this
    generic endpoint doesn't self-document per-report filter schemas.

    `debug=True` logs this read to Qkeee Bot Audit Log, against
    reference_doctype "Report" with reference_name=report_name, since a
    query report isn't itself a DocType record being read.
    """
    cfg = get_env_config(tag)
    params = {"report_name": report_name}
    if filters:
        params["filters"] = json.dumps(filters)
    result = _request(cfg, "GET", "/api/method/frappe.desk.query_report.run", params=params)
    message = result.get("message", {})

    if debug:
        _log_read(cfg, "Report", report_name, requested_by, session_id, persona_code)

    return {
        "report_name": report_name,
        "columns": message.get("columns", []),
        "result": message.get("result", []),
    }


def get_user_roles(tag: str, user: str = "") -> dict:
    """Fetch a user's assigned roles — the standard (heuristic, not
    guaranteed) signal for whether the acting user plausibly holds
    authority for a given write, when no ERPNext Workflow doctype is
    configured for the record type in question (common on a default-
    configured instance — role membership is then the only signal
    available via the REST API). An org with a real approval Workflow
    should be asked about it directly rather than relying on this alone.

    `user` defaults to the empty string, in which case this resolves the
    currently-authenticated user's own roles via the health-check
    endpoint first — get_env_config() has no notion of "which user this
    API key belongs to" (Frappe token auth doesn't expose that directly).
    """
    cfg = get_env_config(tag)
    target = user
    if not target:
        who = _request(cfg, "GET", "/api/method/frappe.auth.get_logged_user")
        target = who.get("message", "")
    path = f"/api/resource/User/{urllib.parse.quote(target)}"
    result = _request(cfg, "GET", path)
    doc = result.get("data", {})
    roles = [r.get("role") for r in doc.get("roles", []) if r.get("role")]
    # An empty roles list is ambiguous: it could mean "confirmed, this user
    # genuinely holds no relevant role" or a lookup that silently came back
    # thin (wrong username resolved, a permission restriction on the User
    # doctype for this API key, etc). Surface that ambiguity explicitly
    # rather than letting the caller treat empty the same as "checked, no
    # authority" — either way the safe default is to treat authority as
    # unconfirmed, but the caller deserves to know which case it's in.
    warning = (
        "No roles returned for this user — could mean the user genuinely "
        "holds no relevant role, or that the lookup didn't resolve "
        "correctly (wrong username, or this API key lacks permission to "
        "read User.roles). Treat as 'authority not confirmed' either way, "
        "but corroborate with the user rather than assuming the former."
        if not roles else ""
    )
    return {"user": target, "roles": roles, "warning": warning}


def _parse_bool_env(raw: str) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    _cli()
