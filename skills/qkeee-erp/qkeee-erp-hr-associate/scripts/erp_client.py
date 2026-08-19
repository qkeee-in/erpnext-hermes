#!/usr/bin/env python3
"""
qkeee-erp-hr-associate connector — read+write copy of the canonical
qkeee-erp-frappe-core ERPNext (Frappe REST API) client, per the self-contained-
copies architecture decision. This persona is read-write-capable (gated
by qkeee_erp.mode) for routine HR transactions (Employee updates, Leave
Application, Attendance, Job Opening, Job Applicant, Interview). Two
capabilities — Offer Letter (Job Offer) and Employee Onboarding — are
NEVER auto-committed regardless of mode; that gate lives in
scripts/render_advisory_draft.py, not here.

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
skill's prompt.

Every write is additionally logged to the `Qkeee Bot Audit Log` doctype
(two-phase: an `Attempted` row inserted before the real write, updated
to `Success`/`Failure` after), and every read is logged there too when
`debug=True` is passed. Session/Message logging (`Qkeee Bot
Session`/`Qkeee Bot Message`) is opt-in per caller via
open_session()/log_message()/close_session(), also debug-gated. All of
this is best-effort — see "Audit logging is best-effort, not a gate"
below for why, and
qkeee-erp-bot-init/references/bot-doctypes-design.md for the full
schema/decision log this implements.
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
SKILL_LABEL = "qkeee-erp-hr-associate"

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


def _tag_env_var(tag: str, suffix: str) -> str:
    sanitized = "".join(c if c.isalnum() else "_" for c in tag.upper()) or "DEFAULT"
    return f"QKEEE_ERP_{sanitized}_{suffix}"


def _qkeee_env_file_path() -> str:
    """Path to the isolated ERPNext-credentials file, deliberately separate
    from Hermes' own profile .env. execute_code/terminal strip ALL env vars
    from the sandbox by default; a var only survives if a loaded skill's
    frontmatter `required_environment_variables` names it exactly (Hermes'
    env_passthrough allowlist) — but a user-chosen --tag can never be
    declared ahead of time in static frontmatter, so QKEEE_ERP_<TAG>_* for
    any tag other than the one named at install time gets silently stripped
    from the sandbox even when it's sitting correctly in the profile's real
    .env. Reading a dedicated file directly (bypassing os.environ/the
    passthrough registry entirely) sidesteps that mismatch, and keeps these
    credentials physically separate from any LLM-provider secret that might
    live in the main .env. HERMES_HOME is unconditionally forwarded into
    every sandbox child regardless of skill declarations (Hermes'
    _HERMES_CHILD_ALLOWED), so it's a reliable anchor even when the
    tag-specific vars themselves aren't. Falls back to CWD for a bare
    non-Hermes shell running this script directly."""
    base = os.environ.get("HERMES_HOME") or os.getcwd()
    return os.path.join(base, "qkeee-erp.env")


def _load_qkeee_env_file() -> dict:
    """Hand-rolled KEY=VALUE parser for qkeee-erp.env (no python-dotenv —
    this module is stdlib-only by design, see module docstring). Comments
    (#) and blank lines skipped; a single layer of surrounding quotes is
    stripped, matching common .env convention. A missing file is not an
    error — callers fall back to os.environ for back-compat with a
    manually-exported shell."""
    path = _qkeee_env_file_path()
    result = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key:
                    result[key] = value
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"WARN: failed to read {path} (non-fatal, falling back to os.environ): {e}", file=sys.stderr)
    return result


_QKEEE_ENV_FILE_CACHE = None


def _qkeee_env() -> dict:
    """Merged config view: qkeee-erp.env file values take precedence over
    os.environ (the file is the source of truth once it exists), os.environ
    remains the fallback for manual/CI runs that still export vars
    directly. Cached per-process — the file doesn't change mid-invocation."""
    global _QKEEE_ENV_FILE_CACHE
    if _QKEEE_ENV_FILE_CACHE is None:
        _QKEEE_ENV_FILE_CACHE = _load_qkeee_env_file()
    merged = dict(os.environ)
    merged.update(_QKEEE_ENV_FILE_CACHE)
    return merged


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
    env = _qkeee_env()
    base_url = env.get(_tag_env_var(tag, "BASE_URL"))
    api_key = env.get(_tag_env_var(tag, "API_KEY"))
    api_secret = env.get(_tag_env_var(tag, "API_SECRET"))

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
            f"Set them in {_qkeee_env_file_path()} (create it if missing — KEY=VALUE per line), "
            f"or export them directly, then retry."
        )

    base_url = base_url.rstrip("/")
    if not base_url.startswith("https://") and not env.get(_tag_env_var(tag, "ALLOW_INSECURE")):
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
        "debug_default": _parse_bool_env(env.get(_tag_env_var(tag, "DEBUG"))),
        "requested_by_default": env.get(_tag_env_var(tag, "REQUESTED_BY"), ""),
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
                    requested_by: str = None, channel: str = None, channel_metadata: dict = None) -> dict:
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
        _log_read(cfg, doctype, None, requested_by, session_id, persona_code, channel, channel_metadata)

    return {"data": rows[:limit], "has_more": has_more, "limit": limit}


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
                  requested_by: str = None, channel: str = None, channel_metadata: dict = None) -> dict:
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
        _log_read(cfg, doctype, name, requested_by, session_id, persona_code, channel, channel_metadata)

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


def _log_read(cfg: dict, doctype: str, name: str, requested_by: str, session_id: str, persona_code: str,
              channel: str = None, channel_metadata: dict = None) -> None:
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
        "channel": channel or "",
        "channel_metadata": json.dumps(channel_metadata) if channel_metadata else None,
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
                            channel: str = None, channel_metadata: dict = None,
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
        "channel": channel or "",
        "channel_metadata": json.dumps(channel_metadata) if channel_metadata else None,
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

def open_session(tag: str, *, user: str, persona_code: str, mode: str, debug_mode: bool = True,
                  channel: str = None, channel_metadata: dict = None) -> str:
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
        "channel": channel,
        "channel_metadata": json.dumps(channel_metadata) if channel_metadata else None,
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
                     channel: str = None, channel_metadata: dict = None,
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
    _VALID_ACTIONS = {"create", "update", "submit", "cancel", "delete"}
    if action not in _VALID_ACTIONS:
        # Live-observed failure mode: a caller swaps the (doctype, action)
        # positional args — e.g. mutate_resource(tag, "create", "Department", ...)
        # instead of (tag, "Department", "create", ...) — which used to surface
        # only as _do_mutate()'s generic "Unknown action 'Department'" once
        # deep inside the call, after the audit-log Attempted row was already
        # written with garbage doctype/action. Catching it here, before any
        # side effect, gives the actual likely cause instead of a symptom.
        hint = (
            f" This looks like doctype/action were swapped — mutate_resource(tag, doctype, "
            f"action, ...) takes doctype BEFORE action; got doctype='{doctype}', action='{action}'."
            if doctype in _VALID_ACTIONS else ""
        )
        raise ConnectorError(
            f"Invalid action '{action}' for doctype '{doctype}'. Expected one of "
            f"{sorted(_VALID_ACTIONS)}.{hint}"
        )
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
        session_id=session_id, persona_code=persona_code, channel=channel, channel_metadata=channel_metadata,
        payload_before=payload_before,
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


def list_configured_tags() -> list:
    """List environment tags with a full var set (BASE_URL+API_KEY+API_SECRET)
    already present in qkeee-erp.env or os.environ. Enables the 'list
    environment tags' part of the environment-configuration capability."""
    tags = {}
    for var_name in _qkeee_env():
        if not var_name.startswith("QKEEE_ERP_"):
            continue
        for suffix in ("_BASE_URL", "_API_KEY", "_API_SECRET"):
            if var_name.endswith(suffix):
                tag = var_name[len("QKEEE_ERP_"):-len(suffix)]
                tags.setdefault(tag, set()).add(suffix)
                break
    return sorted(tag for tag, found in tags.items() if found == {"_BASE_URL", "_API_KEY", "_API_SECRET"})


def _parse_json_arg(flag: str, raw: str, expected_type: type):
    """Parse a CLI flag's JSON value, raising a clean ConnectorError (not a
    raw traceback) on malformed JSON, and a clean error on the right-shaped-
    but-wrong-type JSON (e.g. a dict where a filters list was expected --
    confirmed live to otherwise reach ERPNext as an opaque 500 like
    `TypeError: unhashable type: 'dict'` instead of failing locally with a
    readable message). `expected_type` is `list` or `dict`."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        example = '["name","email"]' if expected_type is list else '{"company": "Acme"}'
        raise ConnectorError(
            f"{flag} must be valid JSON, e.g. {flag} '{example}' - got: {raw!r} ({e})"
        )
    if not isinstance(value, expected_type):
        raise ConnectorError(f"{flag} must be a JSON {expected_type.__name__} - got: {raw!r}")
    return value


def _cli():
    p = argparse.ArgumentParser(description="qkeee-erp-hr-associate connector CLI")
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
    q.add_argument("--filters", help="JSON list, e.g. '[[\"status\",\"=\",\"Active\"]]'")
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

    rp = sub.add_parser("register-persona", help="Idempotent upsert of this persona's Qkeee Bot Persona row (master data, unconditional)")
    rp.add_argument("--persona-code", required=True, help="e.g. qkeee-erp-hr-associate")
    rp.add_argument("--persona-label", required=True, help="display name, e.g. 'HR Associate'")
    rp.add_argument("--default-mode", choices=["read-only", "read-write"], default="read-only",
                     help="this persona's default qkeee_erp.mode")
    rp.add_argument("--non-negotiables", help="free text copied from the persona's SKILL.md, informational only")

    os_ = sub.add_parser("open-session", help="Create a Qkeee Bot Session row (debug-mode logging)")
    os_.add_argument("--user", help="ERPNext user id/email this session acts on behalf of")
    os_.add_argument("--persona-code", required=True, help="e.g. qkeee-erp-hr-associate")
    os_.add_argument("--mode", required=True, choices=["read-only", "read-write"],
                      help="from qkeee_erp.mode at session start")
    os_.add_argument("--no-debug", action="store_true", help="mark debug_mode=False on the Session row (default True)")

    lm = sub.add_parser("log-message", help="Insert a Qkeee Bot Message row (debug-mode logging)")
    lm.add_argument("--session-id", required=True)
    lm.add_argument("--speaker", required=True,
                     choices=["User", "Bot Analysis", "Bot Response", "Bot Action", "System"])
    lm.add_argument("--content", required=True)
    lm.add_argument("--related-capability", help="e.g. 'Leave Application drafting'")
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

    if effective_debug and args.command in ("query", "get", "mutate", "report") and (
        not args.session_id or args.session_id.startswith("local-")
    ):
        print(
            "WARNING: --debug is on but no real session_id is attached to this call "
            "(open-session was never called, or its session_id wasn't threaded through "
            "via --session-id) - this read/write will NOT appear under a Qkeee Bot "
            "Session/Message row, only in Qkeee Bot Audit Log. Call 'open-session' first "
            "and pass its session_id via --session-id.",
            file=sys.stderr,
        )

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
            filters = _parse_json_arg("--filters", args.filters, list)
            fields = _parse_json_arg("--fields", args.fields, list)
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
            payload = _parse_json_arg("--payload", args.payload, dict)
            print(json.dumps(
                mutate_resource(args.tag, args.doctype, args.action, payload, args.name,
                                 args.mode, effective_requested_by,
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
                      requested_by: str = None, channel: str = None, channel_metadata: dict = None) -> dict:
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
        _log_read(cfg, "Report", report_name, requested_by, session_id, persona_code, channel, channel_metadata)

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


def discover_harness_http_tool() -> dict:
    """Harness capability discovery stub — persona/host code should check for a
    harness-native HTTP-capable tool before shelling out to this script.
    Returns a map describing what this script assumes (nothing pre-discovered)."""
    return {"harness_http_tool_detected": False, "fallback": "urllib (this script)"}


def _parse_bool_env(raw: str) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    _cli()
