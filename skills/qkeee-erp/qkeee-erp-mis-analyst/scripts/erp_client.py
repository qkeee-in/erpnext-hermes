#!/usr/bin/env python3
"""
qkeee-erp-mis-analyst connector — read-only-only copy of the canonical
qkeee-erp-core ERPNext (Frappe REST API) client.

Self-contained: stdlib only (urllib). This copy deliberately omits the
write path (mutate_resource, ReadOnlyModeError, the `mutate` CLI
subcommand) entirely — per the module plan, this skill is read-only
always, regardless of `qkeee_erp.mode`. That's a structural guarantee,
not a self-imposed restraint: there is no write call in this file to
invoke. The full read+write connector lives in qkeee-erp-core; sync
read-path changes from there, never add mutate_resource here.

Env/credential model (tagged, not fixed dev/test/qa/prod):
  QKEEE_ERP_<TAG>_BASE_URL
  QKEEE_ERP_<TAG>_API_KEY
  QKEEE_ERP_<TAG>_API_SECRET

<TAG> defaults to "DEFAULT" if the user didn't name one at install.

Audit-trail retrofit: reads can be logged to Qkeee Bot Audit Log when
`debug=True` (best-effort — see
qkeee-erp-bot-init/references/bot-doctypes-design.md). This skill has no
write path, so nothing here touches the two-phase Attempted/Success
write-logging machinery — only the single-shot Read logging applies.
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

    # `payload`/POST here is used only by run_query_report() below, to call
    # a whitelisted *read* method (frappe.desk.query_report.run) that
    # Frappe happens to expose over POST — it runs a report, it does not
    # create/update/submit/cancel/delete a record. This is not the
    # mutate_resource() write path (deliberately absent from this file);
    # don't repurpose this for anything that actually writes data. The
    # audit-log helpers below also use this for logging Read rows, which
    # is itself a write to Qkeee Bot Audit Log — not exempt from that
    # description, just exempt from AUDIT_EXEMPT_DOCTYPES's recursion
    # guard by construction (the log write never re-triggers itself).
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


# --------------------------------------------------------------------------
# Audit logging (Qkeee Bot Audit Log) — read-only side only.
# Best-effort: if the target instance hasn't
# run qkeee-erp-bot-init yet, this silently no-ops and the caller's real
# read proceeds unaffected.
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
    try:
        payload = {"doctype": AUDIT_LOG_DOCTYPE, **fields}
        result = _request(cfg, "POST", f"/api/resource/{urllib.parse.quote(AUDIT_LOG_DOCTYPE)}", payload=payload)
        return (result.get("data") or {}).get("name")
    except Exception:
        # Broad by design: audit logging must never surface a failure mode
        # that could be mistaken for the real read failing.
        return None


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


def query_resource(tag: str, doctype: str, filters: list = None, fields: list = None, limit: int = 20,
                    *, debug: bool = False, session_id: str = None, persona_code: str = None,
                    requested_by: str = None) -> dict:
    """Generic resource query — read any DocType with filters/fields.

    Fetches one extra row beyond `limit` to detect truncation, then trims
    back to `limit` — callers get an explicit `has_more` flag instead of a
    result set that's silently incomplete. Ignoring `has_more` in a report
    (e.g. a GL drill-down that silently drops rows past 20) is a bug in
    the calling report logic, not something this connector can prevent.

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
    p.add_argument("--tag", help="environment tag, from qkeee_erp.active_env (required for health/query)")
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

    args = p.parse_args()

    if args.command in ("health", "query", "report", "get") and not args.tag:
        p.error(f"--tag is required for '{args.command}'")

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
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
