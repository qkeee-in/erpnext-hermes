#!/usr/bin/env python3
"""
qkeee-erp-mis-analyst connector — read-only-only copy of the canonical
qkeee-erp-core ERPNext (Frappe REST API) client.

Self-contained: stdlib only (urllib). This copy deliberately omits the
write path (mutate_resource, _do_mutate, ReadOnlyModeError,
MissingRequesterError, record_comment, the `mutate` CLI subcommand)
entirely — per the module plan, this skill is read-only always,
regardless of `qkeee_erp.mode`. That's a structural guarantee, not a
self-imposed restraint: there is no call in this file that writes to an
arbitrary ERPNext business DocType. The full read+write connector lives
in qkeee-erp-core; sync read-path changes (and shared audit/session/
persona bookkeeping infra) from there, never add mutate_resource here.

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
mutate — only the single-shot Read logging (_log_read) applies. The
Session/Message/Persona bookkeeping helpers below (open_session(),
log_message(), close_session(), ensure_persona_registered()) do write
rows to the Qkeee Bot infra doctypes themselves — that's the same
category of write the pre-existing audit-log insert already made, not
a write to a business DocType, so it's consistent with the read-only
guarantee above.
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
SESSION_DOCTYPE = "Qkeee Bot Session"
MESSAGE_DOCTYPE = "Qkeee Bot Message"
PERSONA_DOCTYPE = "Qkeee Bot Persona"

# Doctypes exempt from audit-wrapping. Mandatory, not optional: without
# this, logging a read of Qkeee Bot Audit Log itself would recurse
# forever. "Comment" is exempt for symmetry with core (this file never
# posts one, since record_comment() is part of the omitted write path).
AUDIT_EXEMPT_DOCTYPES = {
    AUDIT_LOG_DOCTYPE, SESSION_DOCTYPE, MESSAGE_DOCTYPE, PERSONA_DOCTYPE,
    "Comment",
}


class ConnectorError(Exception):
    """Raised for missing config / auth / HTTP failures with a specific, actionable message."""


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

    # `payload`/POST here is used by run_query_report() below, to call a
    # whitelisted *read* method (frappe.desk.query_report.run) that Frappe
    # happens to expose over POST — it runs a report, it does not
    # create/update/submit/cancel/delete a record. It's also used by the
    # audit/session/persona bookkeeping helpers, which write rows to the
    # Qkeee Bot infra doctypes (not exempt from being a "write" in the
    # general sense, just exempt from AUDIT_EXEMPT_DOCTYPES's recursion
    # guard by construction — the log write never re-triggers itself).
    # This is not the mutate_resource() write path (deliberately absent
    # from this file); don't repurpose this for anything that actually
    # writes an arbitrary ERPNext business DocType.
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {cfg['api_key']}:{cfg['api_secret']}")
    req.add_header("Content-Type", "application/json")
    # Python's default urllib UA ("Python-urllib/x.y") is blocked by common
    # WAF/bot-protection (e.g. Cloudflare) fronting production ERPNext
    # instances, returning a 403 that looks like an auth failure but isn't
    # — see qkeee-erp-core/references/connector-reference.md. Always send
    # an explicit UA.
    req.add_header("User-Agent", "qkeee-erp-mis-analyst/1.0")

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

    This confirms connectivity + valid credentials only — it does NOT
    confirm the authenticated user has read permission on the DocTypes
    reports actually query (GL Entry, Account, Cost Center, ...). A
    passing health check followed by a permission-denied error on a
    later query is a distinct failure mode from a connectivity/auth
    failure; report it as such rather than treating query-time
    "PermissionError"/403 the same as a broken connection.
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
    result set that's silently incomplete. Ignoring `has_more` in a report
    (e.g. a GL drill-down that silently drops rows past 20) is a bug in
    the calling report logic, not something this connector can prevent.

    `debug=True` additionally logs this read to Qkeee Bot Audit Log (best-
    effort). Read logging is debug-gated, not unconditional — a
    read-heavy persona like MIS Analyst can generate far more Read calls
    than any other action type, so logging every read unconditionally
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
                  requested_by: str = None) -> dict:
    """Single-resource full-doc GET — the only way to get child-table rows
    (Frappe's list API silently drops Table fields even when requested via
    `fields`; the single-resource GET ignores `fields` entirely and always
    returns everything). This skill is report-driven — prefer
    run_query_report() for standard reports and query_resource() with
    --filters + --fields for custom cuts (~25x cheaper than a full GET
    when child-table data isn't needed). Reach for get_resource() only for
    a genuine single-voucher drill-down that needs its line-item detail.

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


def run_query_report(tag: str, report_name: str, filters: dict = None,
                      *, debug: bool = False, session_id: str = None, persona_code: str = None,
                      requested_by: str = None) -> dict:
    """Run one of ERPNext's own built-in reports server-side, instead of
    hand-aggregating raw GL Entry rows into the same shape.

    This matters for correctness, not just convenience: ERPNext's report
    logic already implements the Finance Book "Include Default FB
    Entries" gate, Accounting Dimension filters, and account-currency vs
    company-currency conversion correctly (see
    references/erpnext-accounting-docs.md) — reimplementing "Profit and
    Loss Statement" from a raw GL Entry query risks silently missing one
    of those and producing a plausible-looking but wrong figure. Prefer
    this for any of ERPNext's standard reports (report_name values:
    "General Ledger", "Trial Balance", "Profit and Loss Statement",
    "Balance Sheet", "Cash Flow Statement", "Accounts Receivable",
    "Accounts Payable", "Budget Variance Report", "Financial Ratios",
    "Party-wise Trial Balance", ...). Fall back to query_resource() on
    GL Entry / Journal Entry directly only for a genuinely custom cut
    that no built-in report covers (e.g. a GL drill-down for one specific
    account+voucher, or an ad hoc filter combination).

    `filters` is a plain dict of report-specific filter values (e.g.
    {"company": "Acme", "from_date": "2026-04-01", "to_date":
    "2026-04-30", "finance_book": None}) — field names vary per report;
    confirm the exact filter keys a given report expects by opening it in
    the ERPNext UI once and inspecting its filter panel, since this
    generic endpoint doesn't self-document per-report filter schemas.

    `debug=True` logs this read to Qkeee Bot Audit Log, against
    reference_doctype "Report" with reference_name=report_name.
    """
    cfg = get_env_config(tag)
    payload = {"report_name": report_name, "filters": filters or {}}
    result = _request(cfg, "POST", "/api/method/frappe.desk.query_report.run", payload=payload)
    message = result.get("message", {})

    if debug:
        _log_read(cfg, "Report", report_name, requested_by, session_id, persona_code)

    return {
        "report_name": report_name,
        "columns": message.get("columns", []),
        "result": message.get("result", []),
    }


# --------------------------------------------------------------------------
# Audit logging (Qkeee Bot Audit Log / Session / Message / Persona)
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
    `local-<timestamp>` fallback shape as open_session()'s own fallback."""
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
        # that could be mistaken for the real read failing. Still warn to
        # stderr so a persistently-failing audit path (e.g. a mandatory
        # field validation error) is visible in logs instead of just an
        # empty Audit Log table with no trace of why.
        print(f"WARN: audit log insert failed (non-fatal): {e}", file=sys.stderr)
        return None


def _log_read(cfg: dict, doctype: str, name: str, requested_by: str, session_id: str, persona_code: str) -> None:
    """Single-shot (no two-phase — nothing to crash into inconsistently
    for a read) best-effort Audit Log row for a debug-mode read."""
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


# --------------------------------------------------------------------------
# Session / Message logging — debug-mode only, opt-in per caller.
#
# Unlike Audit Log reads (debug-gated per-call), Session/Message rows are
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
    bot-doctypes-design.md decision 10). `mode` is recorded for context
    only in this read-only-only skill (this connector never issues a
    write against an ERPNext business DocType regardless of what mode
    string is passed here)."""
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
    p = argparse.ArgumentParser(description="qkeee-erp-mis-analyst connector CLI (read-only)")
    p.add_argument("--tag", help="environment tag, from qkeee_erp.active_env (required for health/query/get/report)")
    p.add_argument("--debug", action="store_true", help="from qkeee_erp.debug — logs reads to Qkeee Bot Audit Log")
    p.add_argument("--session-id", help="from the caller's open_session()")
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

    os_ = sub.add_parser("open-session", help="Create a Qkeee Bot Session row (debug-mode logging)")
    os_.add_argument("--user", required=True, help="ERPNext user id/email this session acts on behalf of")
    os_.add_argument("--persona-code", required=True, help="e.g. qkeee-erp-mis-analyst")
    os_.add_argument("--mode", required=True, choices=["read-only", "read-write"],
                      help="from qkeee_erp.mode at session start")
    os_.add_argument("--no-debug", action="store_true", help="mark debug_mode=False on the Session row (default True)")

    lm = sub.add_parser("log-message", help="Insert a Qkeee Bot Message row (debug-mode logging)")
    lm.add_argument("--session-id", required=True)
    lm.add_argument("--speaker", required=True,
                     choices=["User", "Bot Analysis", "Bot Response", "Bot Action", "System"])
    lm.add_argument("--content", required=True)
    lm.add_argument("--related-capability", help="e.g. 'Trial Balance drill-down'")
    lm.add_argument("--in-reply-to", help="name of the Qkeee Bot Message this turn answers")

    cs = sub.add_parser("close-session", help="Mark a Qkeee Bot Session row Closed/Error")
    cs.add_argument("--session-id", required=True)
    cs.add_argument("--status", choices=["Closed", "Error"], default="Closed")

    args = p.parse_args()

    if args.command in ("health", "query", "report", "get",
                         "register-persona", "open-session", "log-message", "close-session") and not args.tag:
        p.error(f"--tag is required for '{args.command}'")
    if args.command in ("query", "report", "get") and not args.session_id:
        # No --session-id passed (no open_session() call preceded this CLI
        # invocation) — generate a fallback now rather than relying solely
        # on _session_or_fallback() deep inside audit logging, so a
        # --debug query and a --debug report in the same shell session
        # share the visible-to-the-caller id shape consistently.
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
        elif args.command == "report":
            filters = json.loads(args.filters) if args.filters else None
            print(json.dumps(run_query_report(args.tag, args.report_name, filters,
                                               debug=args.debug, session_id=args.session_id,
                                               persona_code=args.persona_code,
                                               requested_by=args.requested_by), indent=2))
        elif args.command == "get":
            print(json.dumps(get_resource(args.tag, args.doctype, args.name, not args.no_strip,
                                           debug=args.debug, session_id=args.session_id,
                                           persona_code=args.persona_code,
                                           requested_by=args.requested_by), indent=2))
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


if __name__ == "__main__":
    _cli()
