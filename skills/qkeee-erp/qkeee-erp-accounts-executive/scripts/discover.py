#!/usr/bin/env python3
"""
qkeee-erp-accounts-executive discovery helper — resolves what's actually installed on a
target ERPNext instance (apps + versions, DocType-to-module-to-app
mapping, live field schema) so a persona skill can reason from real
metadata instead of guessing or trusting static docs.

Self-contained: stdlib only, imports get_env_config()/_request() from the
sibling erp_client.py copy (same connector every qkeee-erp-* skill
carries) rather than re-implementing auth/env resolution. Also imports
_log_read()/AUDIT_EXEMPT_DOCTYPES from the same file so this script's own
metadata reads get the same debug-gated Qkeee Bot Audit Log logging that
query_resource()/get_resource() give erp_client.py's reads, rather than
being invisible to the audit trail.

Non-negotiable this script exists to serve: never propose a field/doctype
that isn't confirmed live on the target instance. GitHub docs and
docs.frappe.io describe the general shape of an app; only this script's
output (backed by /api/resource/DocType/<name> and friends) confirms what
a specific org's instance actually has.
"""

import argparse
import json
import sys
import urllib.parse

from erp_client import AUDIT_EXEMPT_DOCTYPES, ConnectorError, _log_read, _request, get_env_config


def _log(cfg: dict, doctype: str, name: str, debug: bool, requested_by: str,
          session_id: str, persona_code: str) -> None:
    """Debug-gated Read-row logging for this script's own metadata calls
    — same rationale as query_resource()/get_resource() in erp_client.py
    (a read-heavy investigation flow could otherwise out-volume every
    other action type in Qkeee Bot Audit Log). Uses erp_client's own
    _log_read() so the row shape and AUDIT_EXEMPT_DOCTYPES guard stay in
    one place rather than duplicated here."""
    if not debug or doctype in AUDIT_EXEMPT_DOCTYPES:
        return
    _log_read(cfg, doctype, name, requested_by, session_id, persona_code)


def list_installed_apps(tag: str, *, debug: bool = False, requested_by: str = None,
                         session_id: str = None, persona_code: str = None) -> dict:
    """Installed-apps + version list — the same data the ERPNext desk's
    Help > About dialog shows. Frappe exposes this via a whitelisted RPC
    method rather than a REST resource.

    Not verified stable across every Frappe/ERPNext version — if this
    method name has moved (or is blocked by the instance's whitelist
    policy entirely: `PermissionError: ... is not whitelisted`, confirmed
    to happen live on at least one real instance), `_request` raises a
    normal ConnectorError, and the `modules` subcommand (a plain REST
    read, confirmed working even when this RPC is blocked) is the
    expected next step — not a rare fallback.
    """
    cfg = get_env_config(tag)
    try:
        result = _request(cfg, "GET", "/api/method/frappe.utils.change_log.get_versions")
        _log(cfg, "Qkeee Bot Discover Apps", None, debug, requested_by, session_id, persona_code)
        return {"source": "frappe.utils.change_log.get_versions", "apps": result.get("message", result)}
    except ConnectorError as e:
        return {"source": "frappe.utils.change_log.get_versions", "error": str(e),
                "fallback": "use 'modules' subcommand to enumerate apps indirectly via Module Def, "
                            "or ask the user to paste the Help > About dialog contents"}


def list_modules(tag: str, *, debug: bool = False, requested_by: str = None,
                  session_id: str = None, persona_code: str = None) -> dict:
    """Module Def rows — every module/app pairing the instance knows
    about. This is the primary app-discovery path (see list_installed_apps's
    docstring for why `apps` is opportunistic, not primary), and is also
    how a resolved DocType's `module` field gets traced to an owning app
    (see resolve_doctype)."""
    cfg = get_env_config(tag)
    result = _request(cfg, "GET", "/api/resource/Module Def",
                       params={"fields": json.dumps(["name", "app_name"]),
                               "limit_page_length": 0})
    rows = result.get("data", [])
    apps = sorted({r.get("app_name") for r in rows if r.get("app_name")})
    _log(cfg, "Module Def", None, debug, requested_by, session_id, persona_code)
    return {"modules": rows, "apps_seen_via_modules": apps}


# Fields kept from a DocType meta doc — everything else (permissions,
# print settings, form layout hints, etc.) is noise for the "what does
# this doctype actually look like" question this script answers.
_META_FIELD_KEYS = {
    "fieldname", "label", "fieldtype", "reqd", "options", "read_only",
    "hidden", "default", "unique", "in_list_view",
}


def doctype_meta(tag: str, doctype: str, *, debug: bool = False, requested_by: str = None,
                  session_id: str = None, persona_code: str = None) -> dict:
    """Live field schema for one DocType — fieldname/label/fieldtype/reqd
    (mandatory flag)/options (Link target or Select choices) for every
    field actually on this instance, plus module/istable/issubmittable/
    custom flags. Authoritative over any GitHub README or docs.frappe.io
    page, which describe the general shape but not this org's
    customizations (custom fields, altered mandatory flags, etc.).

    Requires System Manager-level read access to the DocType doctype —
    can 403 under a correctly least-privileged shared bot account. A
    caller hitting that should surface it as a permissions gap to fix,
    not treat it as "doctype doesn't exist."
    """
    cfg = get_env_config(tag)
    path = f"/api/resource/DocType/{urllib.parse.quote(doctype)}"
    result = _request(cfg, "GET", path)
    doc = result.get("data") or {}
    fields = [
        {k: f.get(k) for k in _META_FIELD_KEYS if k in f}
        for f in doc.get("fields", [])
    ]
    _log(cfg, "DocType", doctype, debug, requested_by, session_id, persona_code)
    return {
        "doctype": doc.get("name"),
        "module": doc.get("module"),
        "custom": bool(doc.get("custom")),
        "istable": bool(doc.get("istable")),
        "issubmittable": bool(doc.get("issubmittable")),
        "description": doc.get("description"),
        "fields": fields,
    }


def resolve_doctype(tag: str, doctype: str, *, debug: bool = False, requested_by: str = None,
                     session_id: str = None, persona_code: str = None) -> dict:
    """DocType -> module -> app, in one call. This is the core "which app
    owns this doctype" lookup — run it before assuming a doctype belongs
    to any specific qkeee-erp-* persona or to an unmapped custom app.

    `app` is `None` in two distinguishable cases, surfaced separately so
    a caller can't conflate them: a doctype with no `module` at all
    reports `app: null, app_lookup_error: null` (nothing to look up —
    genuinely no module recorded); a doctype whose Module Def lookup
    itself failed (permission denied, network error, a module name that
    doesn't resolve) reports `app: null, app_lookup_error: "<the
    ConnectorError message>"` — a fetch that failed, not a confirmed
    absence. Collapsing both cases to a bare `app: null` risks a false
    "this is custom, no owning app" claim when the lookup had actually
    just errored.
    """
    meta = doctype_meta(tag, doctype, debug=debug, requested_by=requested_by,
                         session_id=session_id, persona_code=persona_code)
    module = meta.get("module")
    app_name = None
    app_lookup_error = None
    if module:
        cfg = get_env_config(tag)
        try:
            path = f"/api/resource/Module Def/{urllib.parse.quote(module)}"
            mod_doc = _request(cfg, "GET", path).get("data") or {}
            app_name = mod_doc.get("app_name")
            _log(cfg, "Module Def", module, debug, requested_by, session_id, persona_code)
        except ConnectorError as e:
            app_lookup_error = str(e)
    return {
        "doctype": meta.get("doctype"),
        "module": module,
        "app": app_name,
        "app_lookup_error": app_lookup_error,
        "custom": meta.get("custom"),
        "istable": meta.get("istable"),
        "issubmittable": meta.get("issubmittable"),
        "field_count": len(meta.get("fields", [])),
    }


def _cli():
    p = argparse.ArgumentParser(description="qkeee-erp-frappe-core discovery CLI")
    p.add_argument("--tag", required=True, help="environment tag, from qkeee_erp.active_env")
    p.add_argument("--debug", action="store_true",
                   help="from qkeee_erp.debug — logs this metadata read to Qkeee Bot Audit Log")
    p.add_argument("--requested-by", help="threaded into the debug-mode audit row, if --debug is set")
    p.add_argument("--session-id", help="from the caller's open_session() — threaded into audit rows")
    p.add_argument("--persona-code", default="qkeee-erp-accounts-executive",
                   help="threaded into audit rows (persona-skill copies should default this to their own name)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("apps", help="installed apps + versions (About-dialog data) — "
                                 "opportunistic, try 'modules' first")
    sub.add_parser("modules", help="Module Def rows + apps seen through them (primary app discovery)")

    m = sub.add_parser("meta", help="live field schema for one DocType")
    m.add_argument("doctype")

    r = sub.add_parser("resolve", help="doctype -> module -> app in one call")
    r.add_argument("doctype")

    args = p.parse_args()
    log_kwargs = dict(debug=args.debug, requested_by=args.requested_by,
                       session_id=args.session_id, persona_code=args.persona_code)

    try:
        if args.command == "apps":
            print(json.dumps(list_installed_apps(args.tag, **log_kwargs), indent=2))
        elif args.command == "modules":
            print(json.dumps(list_modules(args.tag, **log_kwargs), indent=2))
        elif args.command == "meta":
            print(json.dumps(doctype_meta(args.tag, args.doctype, **log_kwargs), indent=2))
        elif args.command == "resolve":
            print(json.dumps(resolve_doctype(args.tag, args.doctype, **log_kwargs), indent=2))
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
