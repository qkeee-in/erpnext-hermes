#!/usr/bin/env python3
# qkeee-erp:read-only-skill
"""
qkeee-erp-mis-analyst connector — read-only-only copy of the canonical
qkeee-erp-frappe-core ERPNext (Frappe REST API) client.

Self-contained: stdlib only (urllib). This copy deliberately omits the
write path (mutate_resource, _do_mutate, ReadOnlyModeError,
MissingRequesterError, record_comment, the `mutate` CLI subcommand)
entirely — per the module plan, this skill is read-only always,
regardless of `qkeee_erp.mode`. That's a structural guarantee, not a
self-imposed restraint: there is no call in this file that writes to an
arbitrary ERPNext business DocType. The full read+write connector lives
in qkeee-erp-frappe-core; sync read-path changes (and shared audit/session/
persona bookkeeping infra) from there, never add mutate_resource here.

**This exact thing happened once already.** A prior `sync_to_personas.py
--apply` run (its `merge_py()` appends any core function whose name is in
`SHARED_FUNCTIONS` and missing from the target — it has no notion of "this
persona deliberately omits this one") silently reintroduced
`mutate_resource`/`_do_mutate`/`record_comment`/`_diff_fields`/
`_audit_update`/`_audit_submit`/`record_audit_log_start`/
`record_audit_log_finish` here as dead, unreachable code (no `mutate` CLI
subcommand ever called them) — and `ReadOnlyModeError`/
`MissingRequesterError`, which `mutate_resource()` raised, were never even
defined in this file, since those are classes and `SHARED_FUNCTIONS`
matching only looks at `def` blocks. Removed 2026-08-18. If a future sync
run reintroduces any of these, delete them again — don't wire them up.

Env/credential model (tagged, not fixed dev/test/qa/prod):
  QKEEE_ERP_<TAG>_BASE_URL
  QKEEE_ERP_<TAG>_API_KEY
  QKEEE_ERP_<TAG>_API_SECRET

<TAG> defaults to "DEFAULT" if the user didn't name one at install.

Audit-trail retrofit: reads can be logged to Qkeee Bot Audit Log when
`debug=True` (best-effort — see
qkeee-erp-bot-init/references/bot-doctypes-design.md). This skill has no
write path against ERPNext business DocTypes, so nothing here touches
the two-phase Attempted/Success write-logging machinery for a real
mutate — only the single-shot Read logging (_log_read) applies.
ensure_persona_registered() below writes a row to the Qkeee Bot
Persona infra doctype itself — that's the same category of write the
pre-existing audit-log insert already made, not a write to a business
DocType, so it's consistent with the read-only guarantee above.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SKILL_LABEL = "qkeee-erp-mis-analyst"

# Qkeee Bot audit-trail doctypes (see qkeee-erp-bot-init). A target
# instance may not have these provisioned yet — every call into them
# below is best-effort and never blocks or fails the caller's actual
# ERPNext read.
AUDIT_LOG_DOCTYPE = "Qkeee Bot Audit Log"
PERSONA_DOCTYPE = "Qkeee Bot Persona"

# Doctypes exempt from audit-wrapping. Mandatory, not optional: without
# this, logging a read of Qkeee Bot Audit Log itself would recurse
# forever. "Comment" is exempt for symmetry with core (this file never
# posts one, since record_comment() is part of the omitted write path).
AUDIT_EXEMPT_DOCTYPES = {
    AUDIT_LOG_DOCTYPE, PERSONA_DOCTYPE,
    "Comment",
}


class ConnectorError(Exception):
    """Raised for missing config / auth / HTTP failures with a specific, actionable message."""


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
# presentation-only HTML/display fields no reporting logic in this skill
# reads. Never strips Link fields, child tables, or any figure a report
# might need. Measured live against <erp-instance> (Sales Order doc, same
# field shapes recur across ERPNext doctypes): ~38% byte reduction.
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


# --------------------------------------------------------------------------
# Audit logging (Qkeee Bot Audit Log / Persona)
#
# Audit logging is best-effort, not a gate. If the target instance hasn't
# run qkeee-erp-bot-init yet, or the audit doctypes are unreachable for any
# reason, every function below swallows the failure and the caller's real
# ERPNext read proceeds unaffected. The alternative — refusing a user's
# actual requested read because internal bookkeeping infra isn't
# provisioned — would regress read availability behind an infra rollout,
# which is a worse failure mode than an occasional unaudited call.
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
    `local-<timestamp>` fallback shape used consistently by CLI callers."""
    return session_id or f"local-{_now_iso()}"


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


def ensure_persona_registered(tag: str, *, persona_code: str, persona_label: str,
                               default_mode: str = "read-only", non_negotiables: str = None,
                               requested_by: str = None, session_id: str = None) -> str:
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

    The create itself IS audited (PERSONA_DOCTYPE was removed from
    AUDIT_EXEMPT_DOCTYPES 2026-08-23 — single-digit-row, create-only,
    non-recursive) via a single-shot `_audit_insert()` call with the
    outcome already known, same collapsed pattern `_log_read()` uses for
    reads (not the two-phase `record_audit_log_start`/`_finish` path
    `mutate_resource()` uses) — deliberately, because those two-phase
    helpers (and the `_audit_update`/`_audit_submit` calls inside
    `record_audit_log_finish`) are `# qkeee-erp:write-path`-marked and
    get excluded from a read-only persona skill's own connector copy
    (e.g. qkeee-erp-mis-analyst), whereas persona registration must keep
    working unconditionally there too — it's master data, not gated by
    `qkeee_erp.mode`. `_audit_insert()` itself carries no such marker, so
    it's present in every skill's copy. Trade-off: this row is never
    submitted (no docstatus lock) and has no `Attempted` pre-image, unlike
    every other audited action — acceptable here since the point is
    visibility of the event, not tamper-evidence on a business write.
    `requested_by` defaults to the tag's own QKEEE_ERP_<TAG>_REQUESTED_BY
    if not passed explicitly — a blank requested_by still doesn't block
    the create (audit logging is best-effort), it just leaves that field
    blank on the row.

    Returns "already_registered" | "created" | "failed". A caller that
    gets "failed" should treat it as the same signal as a `logged_in_as`
    that looks like a personal account — worth proactively surfacing and
    suggesting `qkeee-erp-bot-init`, not silently ignoring."""
    cfg = get_env_config(tag)
    if resource_exists(tag, PERSONA_DOCTYPE, persona_code):
        return "already_registered"
    effective_requested_by = requested_by or cfg.get("requested_by_default") or ""
    try:
        result = _request(cfg, "POST", f"/api/resource/{urllib.parse.quote(PERSONA_DOCTYPE)}", payload={
            "doctype": PERSONA_DOCTYPE,
            "persona_code": persona_code,
            "persona_label": persona_label,
            "default_mode": "Read Write" if default_mode == "read-write" else "Read Only",
            "non_negotiables": non_negotiables or "",
        })
        created_name = (result.get("data") or {}).get("name") or persona_code
        _audit_insert(cfg, {
            "session": _session_or_fallback(session_id),
            "persona_code": persona_code,
            "environment_tag": cfg.get("tag", ""),
            "action": "Create",
            "reference_doctype": PERSONA_DOCTYPE,
            "reference_name": created_name,
            "requested_by": effective_requested_by,
            "timestamp": _now_iso(),
            "status": "Success",
            "payload_after": json.dumps(result.get("data")) if result.get("data") else None,
            "user_approved": "Approved",
            "approval_note": "persona master-data registration, unconditional",
        })
        return "created"
    except ConnectorError as e:
        _audit_insert(cfg, {
            "session": _session_or_fallback(session_id),
            "persona_code": persona_code,
            "environment_tag": cfg.get("tag", ""),
            "action": "Create",
            "reference_doctype": PERSONA_DOCTYPE,
            "reference_name": "",
            "requested_by": effective_requested_by,
            "timestamp": _now_iso(),
            "status": "Failure",
            "error_detail": str(e)[:1900],
            "user_approved": "Approved",
            "approval_note": "persona master-data registration, unconditional",
        })
        print(f"WARN: persona registration failed (non-fatal): {e}", file=sys.stderr)
        return "failed"


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
    p = argparse.ArgumentParser(description="qkeee-erp-mis-analyst connector CLI (read-only)")
    p.add_argument("--tag", help="environment tag, from qkeee_erp.active_env (required for health/query/get/report)")
    p.add_argument("--debug", action="store_true", help="from qkeee_erp.debug — logs reads to Qkeee Bot Audit Log")
    p.add_argument("--session-id", help="plain string correlator threaded into Qkeee Bot Audit Log rows "
                        "(no doctype backs it — pass any string you want related calls to share)")
    p.add_argument("--persona-code", default=SKILL_LABEL, help="threaded into audit rows")
    p.add_argument("--requested-by", help="ERPNext user id/email this session is acting on behalf of")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("list-envs")

    q = sub.add_parser("query")
    q.add_argument("doctype")
    q.add_argument("--filters", help="JSON list, e.g. '[[\"status\",\"=\",\"Open\"]]'")
    q.add_argument("--fields", help="JSON list, e.g. '[\"name\",\"status\"]'")
    q.add_argument("--limit", type=int, default=20)

    r = sub.add_parser("report", help="Run a built-in ERPNext report (e.g. 'Trial Balance')")
    r.add_argument("report_name")
    r.add_argument("--filters", help="JSON object, e.g. '{\"company\":\"Acme\",\"from_date\":\"2026-04-01\"}'")

    g = sub.add_parser("get", help="Single-resource full-doc GET (includes child tables) — noise-stripped by default")
    g.add_argument("doctype")
    g.add_argument("name")
    g.add_argument("--no-strip", action="store_true", help="skip noise-stripping, return the raw doc verbatim")

    rp = sub.add_parser("register-persona", help="Idempotent upsert of this persona's Qkeee Bot Persona row (master data, unconditional)")
    rp.add_argument("--persona-code", required=True, help="e.g. qkeee-erp-mis-analyst")
    rp.add_argument("--persona-label", required=True, help="display name, e.g. 'MIS Analyst'")
    rp.add_argument("--default-mode", choices=["read-only", "read-write"], default="read-only",
                     help="this persona's default qkeee_erp.mode")
    rp.add_argument("--non-negotiables", help="free text copied from the persona's SKILL.md, informational only")

    args = p.parse_args()

    if args.command in ("health", "query", "report", "get",
                         "register-persona") and not args.tag:
        p.error(f"--tag is required for '{args.command}'")
    if args.command in ("query", "report", "get") and not args.session_id:
        # No --session-id passed — generate a fallback now rather than
        # relying solely on _session_or_fallback() deep inside audit
        # logging, so a --debug query and a --debug report in the same
        # shell session share the visible-to-the-caller id shape
        # consistently.
        args.session_id = _session_or_fallback(None)

    # debug/requested-by default from the active TAG's own env vars
    # (QKEEE_ERP_<TAG>_DEBUG / _REQUESTED_BY) — per-tag, not a single
    # global qkeee_erp.debug/.requested_by. --debug/--requested-by on the
    # CLI are a per-call override on top of that default, never a
    # replacement for it. Swallow a resolution failure here — a genuinely
    # missing/misconfigured tag surfaces its own specific error from the
    # real call below.
    tag_debug_default, tag_requested_by_default = False, ""
    if args.command in ("query", "get", "report", "mutate"):
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
            "WARNING: --debug is on but no real session_id was passed via --session-id "
            "for this call - a locally-generated fallback id will be used instead. This "
            "is just a plain string correlator on Qkeee Bot Audit Log rows, not a "
            "reference to any doctype - pass --session-id explicitly if you "
            "want related calls to share one id.",
            file=sys.stderr,
        )

    if args.command == "mutate" and not effective_requested_by:
        p.error(
            "--requested-by is required for 'mutate' (or set "
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
        elif args.command == "report":
            filters = _parse_json_arg("--filters", args.filters, dict)
            print(json.dumps(run_query_report(args.tag, args.report_name, filters,
                                               debug=effective_debug, session_id=args.session_id,
                                               persona_code=args.persona_code,
                                               requested_by=effective_requested_by), indent=2))
        elif args.command == "get":
            print(json.dumps(get_resource(args.tag, args.doctype, args.name, not args.no_strip,
                                           debug=effective_debug, session_id=args.session_id,
                                           persona_code=args.persona_code,
                                           requested_by=effective_requested_by), indent=2))
        elif args.command == "register-persona":
            status = ensure_persona_registered(args.tag, persona_code=args.persona_code,
                                                persona_label=args.persona_label,
                                                default_mode=args.default_mode,
                                                non_negotiables=args.non_negotiables)
            print(json.dumps({"ok": True, "status": status}, indent=2))
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)




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
