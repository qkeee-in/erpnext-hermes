#!/usr/bin/env python3
"""
qkeee-erp-core connector — canonical ERPNext (Frappe REST API) client.

Self-contained: stdlib only (urllib), no third-party deps, so any
persona skill can copy this file verbatim into its own scripts/ dir
per the self-contained-copies architecture decision.

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

Bot account + requester attribution: the API key/secret above must
belong to a dedicated ERPNext integration/bot user, never an
individual's personal login (see Configuration in the module plan).
Every write additionally requires `requested_by` (the ERPNext user
id/email of the human who asked for the change, sourced from
metadata.hermes.config qkeee_erp.requested_by) and, on success, posts
a best-effort audit Comment on the affected record naming that
requester — so ERPNext's own audit trail shows who asked, not just
that the bot acted. See record_comment()/mutate_resource() below.

Audit-trail retrofit: every write is additionally
logged to the `Qkeee Bot Audit Log` doctype (two-phase: an `Attempted`
row inserted before the real write, updated to `Success`/`Failure`
after), and every read is logged there too when `debug=True` is passed.
Session/Message logging (`Qkeee Bot Session`/`Qkeee Bot Message`) is
opt-in per caller via open_session()/log_message()/close_session(),
also debug-gated. All of this is best-effort — see "Audit logging is
best-effort, not a gate" below for why, and
qkeee-erp-bot-init/references/bot-doctypes-design.md for the full
schema/decision log this implements.

Catch-all-specific write gate:
this copy also ships `gated_mutate_resource()`, the actual write entry
point for this skill — it requires a confirmation_token + issued_at from
`render_draft.py`, matching this skill's "advisory-first, always"
non-negotiable in code, not just prompt discipline. Every catch-all
write is gated this way (not just the highest-risk ones, unlike the
narrower per-capability gating in system-admin/fixed-asset-manager's
copies) since nothing here has had the design-time capability review the
eight named persona skills' writes had. See confirm_token.py.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from confirm_token import advisory_write_token, is_fresh

# Persona-skill copies of this file should change this to their own
# skill name (e.g. "qkeee-erp-accounts-executive") so audit comments
# are traceable to the acting skill, not just "core".
SKILL_LABEL = "qkeee-erp-catch-all"

# Qkeee Bot audit-trail doctypes (see qkeee-erp-bot-init). A target
# instance may not have these provisioned yet — every call into them
# below is best-effort and never blocks or fails the caller's actual
# ERPNext read/write.
AUDIT_LOG_DOCTYPE = "Qkeee Bot Audit Log"
SESSION_DOCTYPE = "Qkeee Bot Session"
MESSAGE_DOCTYPE = "Qkeee Bot Message"
PERSONA_DOCTYPE = "Qkeee Bot Persona"

# Doctypes exempt from audit-wrapping. Mandatory, not optional: without
# this, logging a write to Qkeee Bot Audit Log would itself be logged,
# recursing forever. "Comment" is exempt for a related reason — the
# best-effort audit-comment post (record_comment(), below) is itself a
# write; without this exemption every audited write would double-log
# itself (once for the record, once for the Comment documenting it).
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


class StaleConfirmationError(ConnectorError):
    """Raised when a confirmation_token's issued_at is too old (or implausibly
    future) — re-render render_draft.py against current data and reconfirm."""


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
    req.add_header("User-Agent", "qkeee-erp-core/1.0")

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
# reads. Never strips Link fields, child tables, or anything a review step
# would check for validity. Measured live against <erp-instance> (Sales
# Order doc): ~38% byte reduction.
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
# Audit logging (Qkeee Bot Audit Log / Session / Message)
#
# Audit logging is best-effort, not a gate. If the target instance hasn't
# run qkeee-erp-bot-init yet, or the audit doctypes are unreachable for any
# reason, every function below swallows the failure and the caller's real
# ERPNext read/write proceeds unaffected. The alternative — refusing a
# user's actual requested action because internal bookkeeping infra isn't
# provisioned — would regress write availability behind an infra rollout,
# which is a worse failure mode than an occasional unaudited call. This
# mirrors record_comment()'s existing best-effort posture, just applied to
# a bigger piece of infrastructure.
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
    """Single-shot (no two-phase — nothing to crash into inconsistently
    for a read) best-effort Audit Log row for a debug-mode read."""
    if doctype in AUDIT_EXEMPT_DOCTYPES:
        return
    _audit_insert(cfg, {
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
# enforce that gate itself (it has no notion of "the current session's
# debug flag" beyond what the caller passes); it's the caller's job to
# only call these when qkeee_erp.debug is true.
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
    except ConnectorError:
        return None


def ensure_persona_registered(tag: str, *, persona_code: str, persona_label: str,
                               default_mode: str = "read-only", non_negotiables: str = None) -> None:
    """Best-effort idempotent upsert of this persona's Qkeee Bot Persona row.
    Unconditional — NOT debug-gated, not a log (master data, see
    bot-doctypes-design.md's Persona section). No-ops silently if
    Qkeee Bot Persona isn't provisioned yet (bot-init not run) or the
    row already exists; never raises, never blocks the caller."""
    cfg = get_env_config(tag)
    if resource_exists(tag, PERSONA_DOCTYPE, persona_code):
        return
    try:
        _request(cfg, "POST", f"/api/resource/{urllib.parse.quote(PERSONA_DOCTYPE)}", payload={
            "doctype": PERSONA_DOCTYPE,
            "persona_code": persona_code,
            "persona_label": persona_label,
            "default_mode": "Read Write" if default_mode == "read-write" else "Read Only",
            "non_negotiables": non_negotiables or "",
        })
    except ConnectorError as e:
        print(f"WARN: persona registration failed (non-fatal): {e}", file=sys.stderr)


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
    this change, sourced from metadata.hermes.config
    qkeee_erp.requested_by) is required for every write — the connector
    authenticates as a shared bot account, so without this the ERPNext
    audit trail would show only the bot, never who actually asked. On
    success, a best-effort Comment naming the requester is posted to the
    affected record (see record_comment()).

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
            f"Set qkeee_erp.requested_by to the ERPNext user id/email of the person requesting this change."
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


def gated_mutate_resource(tag: str, doctype: str, action: str, payload: dict = None,
                           name: str = None, mode: str = "read-only", requested_by: str = None,
                           *, confirmation_token: str = None, issued_at: int = None,
                           session_id: str = None, persona_code: str = None,
                           approval_note: str = None) -> dict:
    """qkeee-erp-catch-all's write entry point — wraps mutate_resource()
    with the token-gated advisory-first check every catch-all write goes
    through, unconditionally (SKILL.md step 8: advisory-first, always,
    regardless of qkeee_erp.mode). Unlike the narrower per-capability
    gating in system-admin/fixed-asset-manager's copies, EVERY
    create/update/submit/cancel/delete this skill performs is gated here
    — nothing in catch-all has had the design-time capability review that
    lets the eight named persona skills call mutate_resource() directly.

    confirmation_token/issued_at must come from render_draft.py's output
    for this exact (action, doctype, name, payload, requested_by) — see
    confirm_token.py for the token/freshness mechanics. A caller that
    tries to skip render_draft.py (e.g. passing a token computed
    ad hoc, or an old one) is refused here, in code, not just by prompt
    discipline.
    """
    if not confirmation_token or issued_at is None:
        raise ConnectorError(
            f"Refusing {action} on '{doctype}': gated_mutate_resource requires "
            f"confirmation_token + issued_at — render the draft (render_draft.py) first and "
            f"pass its exact token and issued_at here."
        )
    if not is_fresh(int(issued_at)):
        raise StaleConfirmationError(
            "This draft's confirmation has expired or its issued_at is implausible — "
            "re-render render_draft.py against current data and reconfirm before retrying."
        )
    expected = advisory_write_token(action, doctype, name, payload or {}, requested_by, int(issued_at))
    if confirmation_token != expected:
        raise ConnectorError(
            "confirmation_token does not match the (action, doctype, name, payload, "
            "requested_by, issued_at) facts — re-render the draft against the current data "
            "and use that token; don't hand-construct one."
        )

    return mutate_resource(
        tag, doctype, action, payload=payload, name=name, mode=mode, requested_by=requested_by,
        session_id=session_id, persona_code=persona_code,
        user_approved=True, approval_note=approval_note or "gated_mutate_resource: advisory draft confirmed",
    )


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
    p = argparse.ArgumentParser(description="qkeee-erp-core connector CLI")
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
    p.add_argument("--debug", action="store_true",
                   help="from qkeee_erp.debug — logs this read to Qkeee Bot Audit Log (query/get only)")
    p.add_argument("--session-id", help="from the caller's open_session() — threaded into audit rows")
    p.add_argument("--persona-code", help="e.g. qkeee-erp-catch-all — threaded into audit rows")
    # No --user-approved flag here, unlike qkeee-erp-core's CLI: catch-all's 'mutate'
    # is gated_mutate_resource(), which requires --confirmation-token/--issued-at from
    # render_draft.py and always logs user_approved="Approved" once that check passes —
    # there's no unconfirmed write path to flag in the first place.
    p.add_argument("--approval-note", help="free text of what was confirmed (mutate only) — "
                        "defaults to a fixed note if omitted")
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

    m = sub.add_parser("mutate", help="Gated write — requires a token from render_draft.py "
                                       "(this skill's writes are advisory-first, always)")
    m.add_argument("doctype")
    m.add_argument("action", choices=["create", "update", "submit", "cancel", "delete"])
    m.add_argument("--payload", help="JSON object for create/update")
    m.add_argument("--name", help="record name, required for update/submit/cancel/delete")
    m.add_argument("--confirmation-token", required=True,
                    help="from render_draft.py's output for this exact draft")
    m.add_argument("--issued-at", type=int, required=True,
                    help="epoch seconds from render_draft.py's output")

    rp = sub.add_parser("register-persona", help="Idempotent upsert of this persona's Qkeee Bot Persona row (master data, unconditional)")
    rp.add_argument("--persona-code", required=True, help="e.g. qkeee-erp-catch-all")
    rp.add_argument("--persona-label", required=True, help="display name, e.g. 'Catch-All'")
    rp.add_argument("--default-mode", choices=["read-only", "read-write"], default="read-only",
                     help="this persona's default qkeee_erp.mode")
    rp.add_argument("--non-negotiables", help="free text copied from the persona's SKILL.md, informational only")

    os_ = sub.add_parser("open-session", help="Create a Qkeee Bot Session row (debug-mode logging)")
    os_.add_argument("--user", required=True, help="ERPNext user id/email this session acts on behalf of")
    os_.add_argument("--persona-code", required=True, help="e.g. qkeee-erp-catch-all")
    os_.add_argument("--mode", required=True, choices=["read-only", "read-write"],
                      help="from qkeee_erp.mode at session start")
    os_.add_argument("--no-debug", action="store_true", help="mark debug_mode=False on the Session row (default True)")

    lm = sub.add_parser("log-message", help="Insert a Qkeee Bot Message row (debug-mode logging)")
    lm.add_argument("--session-id", required=True)
    lm.add_argument("--speaker", required=True,
                     choices=["User", "Bot Analysis", "Bot Response", "Bot Action", "System"])
    lm.add_argument("--content", required=True)
    lm.add_argument("--related-capability", help="e.g. 'Journal Entry drafting'")
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
        elif args.command == "mutate":
            payload = json.loads(args.payload) if args.payload else None
            print(json.dumps(
                gated_mutate_resource(args.tag, args.doctype, args.action, payload, args.name,
                                       args.mode, args.requested_by,
                                       confirmation_token=args.confirmation_token,
                                       issued_at=args.issued_at,
                                       session_id=args.session_id, persona_code=args.persona_code,
                                       approval_note=args.approval_note),
                indent=2,
            ))
        elif args.command == "register-persona":
            ensure_persona_registered(args.tag, persona_code=args.persona_code,
                                       persona_label=args.persona_label,
                                       default_mode=args.default_mode,
                                       non_negotiables=args.non_negotiables)
            print(json.dumps({"ok": True}, indent=2))
        elif args.command == "open-session":
            session_id = open_session(args.tag, user=args.user, persona_code=args.persona_code,
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


if __name__ == "__main__":
    _cli()
