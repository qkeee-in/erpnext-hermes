#!/usr/bin/env python3
"""
qkeee-erp-bot-init connector — ERPNext (Frappe REST API) client.

Self-contained copy of qkeee-erp-core's erp_client.py, per the
self-contained-copies architecture decision (every qkeee-erp-* skill ships
its own copy rather than depending on qkeee-erp-core at runtime). Synced
from qkeee-erp-core/scripts/erp_client.py, including its audit-trail
retrofit — do not hand-diverge except for the two things noted below.

Deliberate divergences from the core copy (keep these on re-sync):
  1. SKILL_LABEL (below) — so audit comments/log rows trace to this skill.
  2. The CLI's `mutate` subcommand only accepts create/update (not
     submit/cancel/delete) — this skill only ever creates/updates DocType
     and Role records; the choices restriction here is a second guard on
     top of the one already in _do_mutate's target doctypes being
     schema-only in practice, catching an operator typo before it reaches
     the wire.

Audit-trail retrofit note: this copy DOES include the two-phase Audit Log
write path (record_audit_log_start/finish, AUDIT_EXEMPT_DOCTYPES) even
though this skill's own writes (DocType/Role create) target a target
instance that may not have the audit doctypes provisioned yet on a first
run — that's fine: every call into Qkeee Bot Audit Log is best-effort and
exception-swallowed (see "Audit logging is best-effort, not a gate"
below), so pre-provisioning it silently no-ops, and post-provisioning
(e.g. a re-run after a schema change) it starts logging this skill's own
actions like any other write.

Env/credential model (tagged, not fixed dev/test/qa/prod):
  QKEEE_ERP_<TAG>_BASE_URL
  QKEEE_ERP_<TAG>_API_KEY
  QKEEE_ERP_<TAG>_API_SECRET

Unlike the persona skills, this skill's credentials should point at an
ELEVATED (System Manager/Administrator) API key, not the shared
qkeee-erp-bot@<org> steady-state service account — creating DocType/Role
records requires permission that account should not otherwise hold. See
references/bot-doctypes-design.md's "Elevated credentials" section. Note
this is enforced by ERPNext's own server-side permission model (a
non-System-Manager key gets a 403 from the DocType/Role create calls
themselves), not by a pre-check in this file.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SKILL_LABEL = "qkeee-erp-bot-init"

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
    # Python's default urllib UA ("Python-urllib/x.y") is blocked by common
    # WAF/bot-protection (e.g. Cloudflare) fronting production ERPNext
    # instances, returning a 403 that looks like an auth failure but isn't.
    # Always send an explicit UA.
    req.add_header("User-Agent", "qkeee-erp-bot-init/1.0")

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
    """Verify active environment is reachable and authenticated."""
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


# Fields stripped from get_resource() output when strip_noise=True: audit/
# system metadata and presentation-only HTML/display fields. This skill
# always calls get_resource() with strip_noise=False (via resource_exists())
# since it reads back its own just-created DocType/Role records, where
# every field matters — kept here only so the two functions stay a
# faithful sync of core's shape.
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
    """Single-resource full-doc GET.

    strip_noise=True (default) drops audit/system metadata and
    presentation-only HTML fields before returning — see _NOISE_FIELDS.
    This skill's own existence checks call with strip_noise=False so every
    field of a just-created DocType/Role record is visible.

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


def resource_exists(tag: str, doctype: str, name: str) -> bool:
    """404-tolerant existence check — the core existence-check primitive
    this skill's init flow is built on. Never logged, never gated."""
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
# ERPNext read/write proceeds unaffected. For this skill specifically, that
# covers the ordinary first-run case where these doctypes don't exist yet.
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
    top-level keys only, skips noise/metadata fields."""
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
    """Best-effort submit (docstatus lock) of a finished Audit Log row."""
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
    """Single-shot best-effort Audit Log row for a debug-mode read."""
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
    BEFORE the real ERPNext write happens. Returns the row's name, or None
    if the insert itself failed (doctype not provisioned, etc.) — callers
    must treat None as "logging unavailable, proceed anyway", never as a
    reason to abort the real write."""
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
    real write completes (or fails). Best-effort; failures here are
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
# Writes
# --------------------------------------------------------------------------

def mutate_resource(tag: str, doctype: str, action: str, payload: dict = None,
                     name: str = None, mode: str = "read-only", requested_by: str = None,
                     *, session_id: str = None, persona_code: str = None,
                     user_approved: bool = False, approval_note: str = None) -> dict:
    """Generic resource mutate — create/update a DocType or Role record.

    `mode` must be passed explicitly by the caller and this function
    refuses to write unless mode == "read-write" (init_bot.py always
    passes "read-write" — this skill is not gated by qkeee_erp.mode, see
    SKILL.md's non-negotiable section for why).

    `requested_by` is required for every write — on success, a best-effort
    Comment naming the requester is posted to the affected record.
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
    if action not in ("create", "update"):
        raise ConnectorError(
            f"Unknown or unsupported action '{action}' for qkeee-erp-bot-init. "
            f"This skill only creates/updates DocType and Role records — it does not "
            f"submit/cancel/delete."
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
    reference_name = (data or {}).get("name") if isinstance(data, dict) else name
    audit_comment_posted = result.pop("_audit_comment_posted", None) if isinstance(result, dict) else None
    record_audit_log_finish(
        cfg, audit_log_name, status="Success", reference_name=reference_name,
        payload_before=payload_before, payload_after=data if isinstance(data, dict) else None,
        audit_comment_posted=audit_comment_posted,
    )
    return result


def _do_mutate(cfg: dict, doctype: str, action: str, payload: dict, name: str, requested_by: str) -> dict:
    """The actual per-action HTTP dispatch — only create/update, since
    mutate_resource() above already rejects anything else for this skill."""
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

    raise ConnectorError(f"Unknown action '{action}'. Expected create/update.")


def list_configured_tags() -> list:
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
    p = argparse.ArgumentParser(description="qkeee-erp-bot-init connector CLI")
    p.add_argument("--tag", help="environment tag, from qkeee_erp.active_env")
    p.add_argument("--mode", choices=["read-only", "read-write"])
    p.add_argument("--requested-by")
    p.add_argument("--debug", action="store_true",
                   help="logs this read to Qkeee Bot Audit Log (query/get only)")
    p.add_argument("--session-id")
    p.add_argument("--persona-code")
    p.add_argument("--user-approved", action="store_true")
    p.add_argument("--approval-note")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("list-envs")

    q = sub.add_parser("query")
    q.add_argument("doctype")
    q.add_argument("--filters")
    q.add_argument("--fields")
    q.add_argument("--limit", type=int, default=20)

    g = sub.add_parser("get")
    g.add_argument("doctype")
    g.add_argument("name")
    g.add_argument("--no-strip", action="store_true")

    m = sub.add_parser("mutate")
    m.add_argument("doctype")
    # Restricted to create/update — see module docstring's divergence #2.
    m.add_argument("action", choices=["create", "update"])
    m.add_argument("--payload")
    m.add_argument("--name")

    args = p.parse_args()

    if args.command in ("health", "query", "get", "mutate") and not args.tag:
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
