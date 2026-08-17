#!/usr/bin/env python3
"""
qkeee-erp-system-admin — config-change confirmation renderer (Webhook
create, Workflow is_active toggle).

Added after this skill's adversarial review found both of these went
through plain `mutate_resource()` with only a prompt-level single
confirm and no code-level backstop — inconsistent with how aggressively
this persona gates lesser-consequence actions like a Custom Field add.

- **create_webhook**: a Webhook is an outbound data destination — every
  configured webhook is a real SSRF/exfiltration surface, not "inert"
  just because it fires on an event rather than immediately.
- **toggle_workflow**: deactivating a live Workflow can silently stall
  every in-flight document approval on that document type — a blast
  radius comparable to a permission change.

Both get ONE explicit confirm here (not the DOUBLE confirm reserved for
permission changes/destructive actions — these remain a lower risk
tier), but that single confirm is now backed by a confirmation_token +
issued_at the same way, so a caller can't skip straight to the write.

This script NEVER calls ERPNext. It only formats the confirmation.
"""

import json
import sys
import time

from confirm_token import DEFAULT_TOKEN_TTL_SECONDS, config_change_token

KINDS = ("create_webhook", "toggle_workflow")


class RenderError(Exception):
    pass


def render_config_change(kind: str, doctype: str, identifier: str, reason: str,
                          details: str = "", notes: str = "", issued_at: int = None) -> str:
    """
    kind: "create_webhook" or "toggle_workflow".
    doctype: "Webhook" or "Workflow".
    identifier: the webhook's request_url, or the workflow's document_type
      — the fact that most concretely tells the user what's affected.
    reason: required — why this change is being made.
    details: caller-supplied specifics (e.g. webhook_doctype/webhook_docevent/
      request_method for a webhook; current vs. new is_active for a workflow).
    """
    if kind not in KINDS:
        raise RenderError(f"kind must be one of {KINDS}, got {kind!r}")
    if not doctype:
        raise RenderError("doctype is required.")
    if not identifier:
        raise RenderError("identifier is required (request_url for a webhook, document_type for a workflow).")
    if not reason:
        raise RenderError("reason is required — state why this config change is needed.")

    issued_at = int(issued_at) if issued_at is not None else int(time.time())
    token = config_change_token(kind, doctype, identifier, reason, issued_at)

    lines = [
        f"# Config change confirmation — `{kind}` on `{doctype}`",
        "",
        "**Status: NOT APPLIED YET. This is a confirmation request, not a change already "
        "made.**",
        "",
        f"**Reason:** {reason}",
        "",
    ]

    if kind == "create_webhook":
        lines += [
            f"**Target URL:** `{identifier}`",
            "**What happens:** ERPNext will POST data to this URL whenever the configured "
            "event fires. This is an outbound data destination — anything the triggering "
            "document contains leaves this instance and goes to whoever controls that URL. "
            "Confirm the destination is trusted and the URL is correct (typos here silently "
            "send org data to the wrong place) before proceeding.",
        ]
    else:  # toggle_workflow
        lines += [
            f"**Workflow document type:** `{identifier}`",
            "**What happens:** flipping `is_active` on this Workflow changes whether ERPNext "
            "enforces its states/transitions at all for new and in-progress documents of this "
            "type. Deactivating can let documents bypass approval steps entirely; reactivating "
            "can suddenly block edits that were flowing freely. Confirm this is the intended "
            "document type, not a similarly-named one.",
        ]

    if details:
        lines += ["", "**Details:**", details]

    lines += [
        "",
        f"**Confirmation token:** `{token}`  |  **Issued at:** {issued_at} (epoch seconds) — "
        "pass BOTH to `erp_client.gated_config_mutate()`. The call is refused without a "
        "matching token, and refused if issued_at is more than "
        f"{DEFAULT_TOKEN_TTL_SECONDS // 60} minutes old — re-render this confirmation if too "
        "much time has passed since it was shown.",
    ]

    if notes:
        lines += ["", "## Notes", "", notes]

    return "\n".join(lines)


def _cli():
    if len(sys.argv) != 2:
        print("Usage: render_config_change.py <path-to-json-input>", file=sys.stderr)
        sys.exit(2)

    try:
        with open(sys.argv[1], "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        print(f"ERROR: input file not found: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: {sys.argv[1]} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        out = render_config_change(
            kind=payload["kind"],
            doctype=payload["doctype"],
            identifier=payload.get("identifier", ""),
            reason=payload.get("reason", ""),
            details=payload.get("details", ""),
            notes=payload.get("notes", ""),
            issued_at=payload.get("issued_at"),
        )
    except (RenderError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(out)


if __name__ == "__main__":
    _cli()
