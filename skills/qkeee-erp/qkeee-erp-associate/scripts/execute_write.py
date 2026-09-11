#!/usr/bin/env python3
"""
qkeee-erp-associate write CLI — the one entry point for every create/
update/submit/cancel/delete this skill issues, domain-scoped or not.

## Why this exists (F1, .scratch/hermes-erp-bot-reliability/spec.md)

Before this file, there was no single write path: a domain-scoped write
needed `from domains import <slug>` first (to trigger
register_domain_allowlist() at import time — `core/client.py`'s own bare
`mutate --domain <slug>` CLI subcommand can't do this itself, since it
never imports any `domains/*.py` module; run standalone, `--domain
procurement` 404s with "domain has no registered ALLOWED_WRITE_DOCTYPES"
even though `domains/procurement.py` genuinely declares one). The
practical result, live-observed on dev-hermes 2026-09-11: an agent
hand-writes a fresh Python script for every single write instead
(`from domains import procurement; procurement.mutate(...)`) — and a
freshly hand-written script is exactly where `session_id`/
`channel_metadata`/`latest_prompt` keep getting left blank (hardcoded
`SID = ""`, no `channel_metadata` dict ever built), because nothing
forces the same context-resolution code to run twice the same way.

This file removes the reason to hand-write that script: it imports every
`domains/*.py` module up front (so any `--domain` value's allowlist is
registered before the write fires, regardless of which one), and it
requires the audit-context flags loudly rather than letting them go
quietly missing — see `_cli()`'s pre-flight warnings below. It does not
change `core/client.py` itself; it's a caller, same tier as `discover.py`.

## Two write shapes, one flag decides which

- `--domain <slug>` given → dispatches to that `domains/<slug>.py`
  module's own `mutate()`, NOT `core.client.mutate_resource()` directly.
  This matters: a domain module's `mutate()` is where that domain layers
  its own doctype-specific rules on top of the generic allowlist gate —
  procurement's Supplier-KYC-completeness check (F2, .scratch/
  hermes-erp-bot-reliability/spec.md) is the first example. Calling
  `mutate_resource(domain=...)` directly would skip that rule entirely;
  going through the domain module's `mutate()` is what makes it
  unbypassable regardless of which caller fires the write. `<slug>` must
  match one of `scripts/domains/*.py`'s `DOMAIN_NAME` (accounts,
  fixed_assets, hr_payroll, inventory, mis, procurement, sales,
  system_admin).
- `--domain` omitted → `core.client.gated_mutate_resource(...)` — the
  advisory-token path for a doctype no domain owns (Item today — see F3/
  issue 01). `--confirmation-token`/`--issued-at` are required in this
  shape; `gated_mutate_resource()` itself refuses to proceed without
  them. So is `--user-confirmation-text` now (F5, .scratch/
  hermes-erp-bot-reliability/spec.md) — the literal text of the user's
  own reply, which must contain the confirmation code
  `confirm_token.py`'s CLI prints alongside the token. This is what
  actually ties execution to a human having seen the rendered draft; the
  token match alone only proves the payload wasn't altered since render
  — see `confirm_token.confirmation_code()`'s own docstring for the
  honest limits of what this does and doesn't prove.

## Audit context is loud here, not silent

`core/client.py`'s own `mutate` CLI subcommand silently substitutes a
synthetic `local-<timestamp>` session_id when `--session-id` is omitted
(see `_cli()`'s `if ... not args.session_id: args.session_id =
_session_or_fallback(None)`) — the exact mechanism that let F1 happen
without a trace. This script does NOT hide that substitution: omitting
`--session-id`, `--channel-metadata`, or `--latest-prompt` prints a WARN
to stderr naming exactly what's missing, before the write fires. Passing
them is still not code-enforced (F1's decided course was this CLI +
doc instruction, not a hard `MissingContextError` — see spec.md F1) —
the loud warning is the middle ground: an agent can still choose to
proceed, but never by accident or unnoticed.

Every result also gets its `_audit_log_status` checked here and, when
it's anything other than "ok"/"exempt", printed as a second, separate
warning — `00-conventions.md`'s GRC baseline already says to do this;
this script does it uniformly so no caller has to remember to.

## `--purchase-sourced-item` (F6, F9)

A thin, Item-specific conditional, not generic write-path logic: when
`--doctype Item --action create` and this flag is set, the payload is run
through `item_write_helpers.apply_purchase_sourced_item_defaults()` before
the write fires — defaults `is_purchase_item=1, is_sales_item=0` (F9:
nothing in a purchase document supports "the org resells this"), and
refuses a bare `standard_rate` key outright (F6: that auto-creates a
Standard SELLING Item Price from what was actually a purchase cost — see
`item_write_helpers.py`'s own docstring for the fix). See that file
rather than this one for the actual logic.

## Schema-first attribute mapping (issue 01, F3/F4 fold-in)

Every `--action create`/`update`, for every `--doctype`, unconditionally
(not opt-in the way `--purchase-sourced-item` is): before dispatch,
`_apply_schema_mapping()` runs the payload (or `--staged-fields`, when
doc-extraction's confidence-rated output is being written directly)
through `schema_mapping.map_payload_for_write()` against that doctype's
live field schema, so a field only gets left out because it genuinely
isn't on the instance — never because a domain doc's curated field list
didn't happen to mention it. Degrades to the payload as given (warn, not
refuse) if the schema fetch itself fails; hard-refuses only when a
`--staged-fields` entry is both low-confidence and unmatched against the
live schema. See `schema_mapping.py`'s own module docstring for the full
design rationale (why this lives here and not inside
`mutate_resource()`/`gated_mutate_resource()`, the fuzzy-match
confirmation story, the F2/F4 interaction decisions).
"""

import argparse
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Import every domain module for its side effect (register_domain_allowlist()/
# register_domain_token_gate() at import time) — this is what lets --domain
# work standalone, in a single process, regardless of which domain is named.
# domains/__init__.py deliberately does NOT do this itself (see its own
# docstring) — manufacturing and doc_extraction have no write path at all,
# so they're not here either.
from domains import (  # noqa: F401
    accounts,
    fixed_assets,
    hr_payroll,
    inventory,
    mis,
    procurement,
    sales,
    system_admin,
)
from core.client import (
    ConnectorError,
    gated_mutate_resource,
    resolve_requested_by,
)
from core.client import _parse_json_arg  # noqa: F401 -- shared JSON-flag parsing, same errors as core/client.py's own CLI
from item_write_helpers import (
    BareStandardRateOnPurchaseSourcedItemError,
    apply_purchase_sourced_item_defaults,
)
import schema_mapping

# name -> module, so --domain dispatches to that domain's OWN mutate() —
# never core.client.mutate_resource() directly — see module docstring.
_DOMAIN_MODULES = {
    accounts.DOMAIN_NAME: accounts,
    fixed_assets.DOMAIN_NAME: fixed_assets,
    hr_payroll.DOMAIN_NAME: hr_payroll,
    inventory.DOMAIN_NAME: inventory,
    mis.DOMAIN_NAME: mis,
    procurement.DOMAIN_NAME: procurement,
    sales.DOMAIN_NAME: sales,
    system_admin.DOMAIN_NAME: system_admin,
}
_KNOWN_DOMAINS = set(_DOMAIN_MODULES)


def _warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def _apply_schema_mapping(args, payload: dict, effective_requested_by: str, channel_metadata: dict,
                           staged_fields: list, confirmed_mappings: dict) -> dict:
    """issue 01 (schema-first attribute mapping, .scratch/hermes-erp-bot-
    reliability/issues/01-schema-first-attribute-mapping.md) — runs before
    every create/update dispatch below. See schema_mapping.py's own module
    docstring for why this lives here rather than inside
    mutate_resource()/gated_mutate_resource(). Loud-not-blocking for a
    schema-fetch failure or a suggested/unmatched field (mirrors
    `_preflight_context_check()`'s posture); hard-refuses only on a
    `high_risk` field — a staged field that's both low-confidence and
    unmatched against the live schema (F4's fold-in) — via ConnectorError,
    caught by this file's existing top-level ConnectorError handler."""
    mapping = schema_mapping.map_payload_for_write(
        args.tag, args.doctype, payload, requested_by=effective_requested_by,
        staged_fields=staged_fields, confirmed_mappings=confirmed_mappings,
        session_id=args.session_id, domain_code=args.domain_code,
        channel=args.channel, channel_metadata=channel_metadata,
        prompt_summary=args.prompt_summary, latest_prompt=args.latest_prompt,
    )
    if mapping["status"] == "unavailable":
        _warn(f"schema-first field mapping unavailable for '{args.doctype}' on tag '{args.tag}' "
              f"({mapping['detail']}) — proceeding with the payload as given, unmapped. See "
              f"issue 01, .scratch/hermes-erp-bot-reliability/issues/"
              f"01-schema-first-attribute-mapping.md.")
        return payload
    if mapping["suggested_mappings"]:
        _warn(f"{len(mapping['suggested_mappings'])} field(s) matched a live schema field only "
              f"via a synonym hint, NOT applied without confirmation: {mapping['suggested_mappings']!r} "
              f"— re-run with --confirmed-mappings including the ones the user confirms.")
    if mapping["unmatched"]:
        _warn(f"field(s) with no matching live schema field on '{args.doctype}', dropped from "
              f"the payload actually sent: {mapping['unmatched']!r}")
    if mapping["status"] == "high_risk":
        raise ConnectorError(
            f"Refusing {args.action} on '{args.doctype}': staged field(s) "
            f"{mapping['high_risk']!r} are BOTH low-confidence AND unmatched against the live "
            f"schema — the highest-risk combination (issue 01's F4 fold-in). Resolve manually "
            f"with the user (correct the source value, or supply the right live fieldname via "
            f"--confirmed-mappings) before retrying."
        )
    return mapping["payload"]


def _preflight_context_check(args) -> None:
    """Loud, not blocking — see module docstring. Names exactly what's
    missing so the caller can't miss it the way F1's hand-written scripts
    did."""
    if not args.session_id:
        _warn("--session-id not given — Qkeee Bot Audit Log will fall back to a synthetic "
              "local-<timestamp> session, unrelated to the real platform thread. Resolve the "
              "actual session id fresh for this logical session (00-conventions.md's GRC "
              "baseline) and pass it explicitly.")
    if not args.channel_metadata:
        _warn("--channel-metadata not given — the audit row won't carry the platform space/"
              "thread/channel id this write came from (F1, .scratch/hermes-erp-bot-reliability/"
              "spec.md). Pass the channel's own tracing detail as a JSON object, e.g. "
              '\'{"space": "spaces/AAQ...", "thread": "threads/HzG..."}\'.')
    if not args.latest_prompt:
        _warn("--latest-prompt not given — only --prompt-summary (a paraphrase) will be on the "
              "audit row, not the user's verbatim request. Pass the literal most-recent user "
              "message that drove this write.")


def _cli():
    p = argparse.ArgumentParser(
        description="qkeee-erp-associate write CLI — the one entry point for every "
                    "create/update/submit/cancel/delete, domain-scoped or not. See this "
                    "file's own module docstring for why it exists (F1)."
    )
    p.add_argument("--tag", required=True, help="environment tag, from qkeee_erp.active_env")
    p.add_argument("--mode", required=True, choices=["read-only", "read-write"],
                   help="from qkeee_erp.mode — read-write required for any write to actually fire")
    p.add_argument("--requested-by", required=True,
                   help="ERPNext user id/email of the requester, resolved fresh from the live "
                        "inbound channel identity — no default, no fallback")
    p.add_argument("--doctype", required=True)
    p.add_argument("--action", required=True, choices=["create", "update", "submit", "cancel", "delete"])
    p.add_argument("--payload", help="JSON object for create/update")
    p.add_argument("--name", help="record name, required for update/submit/cancel/delete")
    p.add_argument("--domain", choices=sorted(_KNOWN_DOMAINS),
                   help="registered domain name — routes through mutate_resource()'s reviewed "
                        "allowlist. Omit for a doctype no domain owns (e.g. Item) to route "
                        "through gated_mutate_resource() instead (requires "
                        "--confirmation-token/--issued-at).")
    p.add_argument("--confirmation-token",
                   help="required when --domain is omitted; also required when the named "
                        "domain has registered this --action via register_domain_token_gate() "
                        "(normally submit/cancel/delete)")
    p.add_argument("--issued-at", type=int, help="epoch seconds the confirmation token was computed at")
    p.add_argument("--user-confirmation-text",
                   help="required when --domain is omitted (gated_mutate_resource path): literal "
                        "text of the user's own reply, must contain confirm_token."
                        "confirmation_code(confirmation_token) — see F5, .scratch/"
                        "hermes-erp-bot-reliability/spec.md. Not applicable to a domain-scoped "
                        "write's own submit/cancel/delete token gate.")
    p.add_argument("--user-approved", action="store_true",
                   help="pass only when this write's confirm stage genuinely ran with the user "
                        "first — logged for later scanning, not enforced as a gate")
    p.add_argument("--approval-note", help="free text of what was confirmed")
    p.add_argument("--session-id", help="plain string correlator for Qkeee Bot Audit Log rows — "
                        "resolve fresh per logical session, see 00-conventions.md's GRC baseline")
    p.add_argument("--domain-code", default="qkeee-erp-associate",
                   help="threaded into audit rows (defaults to the skill name; a domain write "
                        "auto-labels as qkeee-erp-associate/<domain> regardless)")
    p.add_argument("--channel", help="conversation surface, e.g. Google Chat/Discord/Telegram/"
                        "WhatsApp/Email/Web/Slack/CLI/API/Other")
    p.add_argument("--channel-metadata", help="JSON object of channel-specific tracing detail "
                        "(the chat space/thread id, etc.)")
    p.add_argument("--prompt-summary", help="one-line summary of the user request driving this write")
    p.add_argument("--latest-prompt", help="verbatim most-recent user prompt from the driving chat")
    p.add_argument("--kyc", help='procurement.py-only: JSON object, e.g. \'{"address": {...}, '
                        '"contact": {...}}\' — required (or --kyc-waiver-confirmed) for a '
                        "Supplier create; see procurement.md's KYC write order (F2)")
    p.add_argument("--kyc-waiver-confirmed", action="store_true",
                   help="procurement.py-only: pass only when the user has explicitly confirmed "
                        "proceeding with a Supplier create without KYC")
    p.add_argument("--purchase-sourced-item", action="store_true",
                   help="Item-only, --action create: apply item_write_helpers."
                        "apply_purchase_sourced_item_defaults() to --payload before writing — "
                        "defaults is_purchase_item=1/is_sales_item=0 (F9) and refuses a bare "
                        "standard_rate key (F6). See item_write_helpers.py.")
    p.add_argument("--staged-fields",
                   help="issue 01 (schema-first attribute mapping), --action create/update only: "
                        "JSON array of doc-extraction's staged-report entries, e.g. "
                        '\'[{"field": "HSN", "value": "84713090", "confidence": "high"}]\' — '
                        "matched against the live doctype schema instead of --payload's own keys "
                        "when given. See schema_mapping.py.")
    p.add_argument("--confirmed-mappings",
                   help="issue 01: JSON object {candidate_key: live_fieldname} confirming one or "
                        "more schema_mapping.py suggested_mappings entries the user has explicitly "
                        "signed off on — the only way a fuzzy/synonym-matched field ever reaches "
                        "the payload actually sent.")

    args = p.parse_args()

    kyc_applicable = (args.domain == "procurement" and args.doctype == "Supplier"
                       and args.action == "create")
    if (args.kyc or args.kyc_waiver_confirmed) and not kyc_applicable:
        p.error("--kyc/--kyc-waiver-confirmed only apply to --domain procurement --doctype "
                "Supplier --action create — they'd be silently ignored here otherwise.")

    if args.purchase_sourced_item and not (args.doctype == "Item" and args.action == "create"):
        p.error("--purchase-sourced-item only applies to --doctype Item --action create — "
                "it'd be silently ignored here otherwise.")

    try:
        payload = _parse_json_arg("--payload", args.payload, dict)
        channel_metadata = _parse_json_arg("--channel-metadata", args.channel_metadata, dict)
        kyc = _parse_json_arg("--kyc", args.kyc, dict)
        staged_fields = _parse_json_arg("--staged-fields", args.staged_fields, list)
        confirmed_mappings = _parse_json_arg("--confirmed-mappings", args.confirmed_mappings, dict)
        if args.purchase_sourced_item:
            payload = apply_purchase_sourced_item_defaults(payload or {})
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except BareStandardRateOnPurchaseSourcedItemError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    effective_requested_by = resolve_requested_by(args.requested_by)
    _preflight_context_check(args)

    if args.action in ("create", "update"):
        try:
            payload = _apply_schema_mapping(args, payload, effective_requested_by, channel_metadata,
                                             staged_fields, confirmed_mappings)
        except ConnectorError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.staged_fields or args.confirmed_mappings:
        p.error("--staged-fields/--confirmed-mappings only apply to --action create/update — "
                "they'd be silently ignored here otherwise.")

    common = dict(
        payload=payload, name=args.name, mode=args.mode, requested_by=effective_requested_by,
        session_id=args.session_id, domain_code=args.domain_code,
        channel=args.channel, channel_metadata=channel_metadata,
        approval_note=args.approval_note,
        prompt_summary=args.prompt_summary, latest_prompt=args.latest_prompt,
    )

    try:
        if args.domain:
            domain_kwargs = {}
            if kyc_applicable:
                domain_kwargs.update(kyc=kyc, kyc_waiver_confirmed=args.kyc_waiver_confirmed)
            result = _DOMAIN_MODULES[args.domain].mutate(
                args.tag, args.doctype, args.action,
                user_approved=args.user_approved,
                confirmation_token=args.confirmation_token, issued_at=args.issued_at,
                **domain_kwargs, **common,
            )
        else:
            if not args.confirmation_token or args.issued_at is None:
                p.error("--confirmation-token and --issued-at are required when --domain is "
                        "omitted (gated_mutate_resource path) — render the draft first via "
                        "confirm_token.py's advisory-token CLI over the exact facts shown to "
                        "and confirmed by the user, then pass its output here unchanged.")
            if not args.user_confirmation_text:
                p.error("--user-confirmation-text is required when --domain is omitted "
                        "(gated_mutate_resource path) — the literal text of the user's own "
                        "reply, containing the confirmation_code shown in the rendered draft. "
                        "See F5, .scratch/hermes-erp-bot-reliability/spec.md.")
            result = gated_mutate_resource(
                args.tag, args.doctype, args.action,
                confirmation_token=args.confirmation_token, issued_at=args.issued_at,
                user_confirmation_text=args.user_confirmation_text,
                **common,
            )
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    audit_status = result.get("_audit_log_status") if isinstance(result, dict) else None
    if audit_status not in ("ok", "exempt"):
        _warn(f"this write's Qkeee Bot Audit Log status is {audit_status!r}, not 'ok'/'exempt' — "
              f"the write itself succeeded, but it is NOT reliably in the audit trail. Surface "
              f"this to the user rather than reporting a plain success (00-conventions.md's GRC "
              f"baseline: \"A 'success' from a best-effort write is not proof it landed\").")

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _cli()
