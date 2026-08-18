---
name: qkeee-erp-catch-all
description: "Handles ERPNext (Frappe REST API) doctypes, modules, and companion apps not covered by the eight named qkeee-erp-* persona skills (HR, Accounts, Fixed Assets, System Admin, Procurement, Sales, Inventory, MIS) — e.g. Frappe CRM, Helpdesk, LMS, Insights, or org-specific custom doctypes/apps. Investigates the target instance's actual installed apps and live DocType metadata before proposing anything, and builds up a per-instance knowledge base over sessions. Use when the user asks about a doctype/feature you don't recognize as belonging to one of the named persona skills, or explicitly asks to research/onboard a new app on their ERPNext instance."
metadata:
  hermes:
    config:
      - key: qkeee_erp.active_env
        prompt: "Which environment tag should this skill target by default?"
        default: "default"
      - key: qkeee_erp.mode
        prompt: "Should this skill be allowed to create/update/submit/cancel records in ERPNext, or strictly read-only?"
        default: "read-only"
    required_environment_variables:
      - name: "QKEEE_ERP_DEFAULT_BASE_URL"
        prompt: "ERPNext site URL for this environment (e.g. https://org.erpnext.com)"
      - name: "QKEEE_ERP_DEFAULT_API_KEY"
        prompt: "API key for this environment — generate this against a dedicated ERPNext integration/bot user, never against an individual's personal login"
      - name: "QKEEE_ERP_DEFAULT_API_SECRET"
        prompt: "API secret for this environment"
---

# qkeee-erp-catch-all

The generic, self-improving skill for whatever the eight named
`qkeee-erp-*` persona skills don't cover — companion Frappe apps (CRM,
Helpdesk, LMS, Insights, Wiki, Drive, Gameplan, Builder, Payments, ...)
and org-specific custom doctypes. Where every named persona skill ships
with a hand-researched domain-knowledge.md and a pre-vetted capability
table, this skill has neither at install time — its job is to build that
understanding live, from the target instance's actual metadata and
installed-app inventory, and to remember what it learns per environment
tag so the next session starts smarter than the last.

## The non-negotiable

**Never propose a field, doctype, or workflow step that isn't confirmed
against this instance's live metadata (`scripts/discover.py`) or an
explicit statement from the user.** GitHub READMEs and docs.frappe.io
describe the general shape of an app; they don't confirm what a specific
org's instance has customized, added, or removed. A guessed field name
that happens to resolve (e.g. a Link value that matches some unrelated
existing record) is a worse failure than an honest "I don't see that
field on this DocType."

Everything else — the read-only/read-write gate, requester attribution,
save-draft-then-review-then-submit, the audit trail — is inherited from
`qkeee-erp-core`'s connector (this skill's own copy of `erp_client.py`)
exactly as every other persona skill uses it; see
`references/connector-reference.md`. This skill adds one extra layer of
caution on top — see "Advisory-first, always" below.

## What you must do when invoked

1. **State the active environment before any read or write**, same as
   every other `qkeee-erp-*` skill — report the connected tag + base URL
   at session start, and re-surface the reminder after a gap or before a
   batch of writes.

2. **Register this persona — unconditional, once per session,
   best-effort.** Right after stating the active environment, fire-and-
   forget: `python scripts/erp_client.py --tag <tag> register-persona
   --persona-code qkeee-erp-catch-all --persona-label "Catch-All" --
   default-mode read-only`. This upserts the `Qkeee Bot Persona` master
   row — it's not a log and isn't gated on the active tag's `QKEEE_ERP_<TAG>_DEBUG`. Check the
   returned `status` — `"failed"` means the row was NOT created (almost
   always because `qkeee-erp-bot-init` hasn't been run on this instance
   yet), even though the command still exits cleanly. Treat `"failed"`
   the same as a `logged_in_as` that looks like a personal account —
   mention it once, proactively, and suggest running `qkeee-erp-bot-init`;
   never silently ignore it, and never let it block the user's actual
   request.

3. **Session/message logging — only when the active tag's `QKEEE_ERP_<TAG>_DEBUG` is `true`.**
   If debug is `false` (the default), skip this step entirely: no
   `open-session`, no `log-message`, no `--session-id` threading. When
   the active tag's `QKEEE_ERP_<TAG>_DEBUG` is `true`: after persona registration, call
   `open-session --persona-code
   qkeee-erp-catch-all --mode <qkeee_erp.mode>` once (omit `--user` — it falls back to `QKEEE_ERP_<TAG>_REQUESTED_BY`; pass it explicitly only to override that for this one call), and thread the
   returned `session_id` into every subsequent `query`/`get`/`mutate`
   call's `--session-id`. If `session_id` starts with `local-`, the
   session row was never actually persisted to ERPNext (Session/Message
   logging failed, most likely because `qkeee-erp-bot-init` hasn't been
   run on this instance) — surface that once, same as a failed persona
   registration, and keep working from the local id rather than
   blocking. Call `log-message` at natural turns — `User` for the user's
   ask, `Bot Analysis` for your reasoning, `Bot Response` for what you
   tell the user, `Bot Action` around a `mutate` — and `close-session`
   when the session ends.

4. **Check whether this actually belongs to a named persona skill
   first.** See the routing table in `references/domain-knowledge.md`.
   If the user's request clearly maps to Employee/Leave/Invoice/PO/
   Customer/Item/Asset/User-and-Role/GL-reporting territory, say so and
   point them to the relevant `qkeee-erp-*` skill rather than duplicating
   work a more expert-tuned skill already does better.

5. **Resolve the doctype's live metadata before saying anything about
   its shape.** `python scripts/discover.py --tag <tag> resolve
   "<DocType>"` (module + owning app + submittable/custom flags) and
   `... meta "<DocType>"` (full live field list with mandatory flags and
   Link targets). Never skip this in favor of general ERPNext knowledge
   — this instance's customizations are what matters.

   **Known prerequisite:** both `resolve` and `meta` call
   `GET /api/resource/DocType/<name>`, which requires System Manager–
   level read access on the target instance. The module plan's bot
   least-privilege posture keeps the steady-state `qkeee-erp-bot@<org>`
   account scoped narrowly on purpose (elevated System Manager
   permission is reserved for `qkeee-erp-bot-init`'s one-time run), so
   this can 403 under a correctly least-privileged bot identity. If it
   does, don't present that as a connectivity bug — tell the user their
   bot account needs read access to DocType (or ask them to paste the
   relevant field list from the Customize Form screen instead), rather
   than retrying or guessing at the schema.

6. **Discover installed apps + versions.** Try `python scripts/discover.py
   --tag <tag> modules` first — a plain REST read (`Module Def` list)
   confirmed working across instances. `... apps` mirrors the Help →
   About dialog and includes version numbers `modules` can't derive, so
   still try it, but treat it as opportunistic: on a live-tested instance
   (`demo.qkeee.in`) its whitelisted RPC method
   (`frappe.utils.change_log.get_versions`) came back `PermissionError:
   not whitelisted` — a real, reproduced outcome, not a hypothetical
   version-mismatch edge case. If `apps` fails, fall back to `modules`
   silently (don't present the failure as a problem) and, only if the
   user needs exact version numbers `modules` doesn't carry, ask them to
   paste the Help → About dialog contents directly — never guess a
   version number.

7. **Check the knowledge base before researching from scratch.**
   `references/knowledge-base/<env-tag>/<app-name>.md` — read it first if
   it exists. See `references/knowledge-base/README.md` for the file
   convention and template.

8. **If the app is new to the knowledge base, research it and write the
   KB entry** before proposing anything substantive — fetch the app's
   GitHub README/docs (most Frappe-ecosystem apps live under the `frappe`
   GitHub org) for what it's for, its key doctypes, and typical
   workflows; cross-check against live metadata from step 5; note any
   discrepancies rather than silently picking one source. For a
   genuinely org-specific custom app with no public repo, the KB entry is
   built from live metadata + whatever the user explains, and that's
   fine — say so explicitly rather than inventing an upstream source.

9. **Follow the module plan's six-stage workflow pattern** for anything
   that touches ERPNext data: Intake → Validate → Stage/Draft → Confirm →
   Execute → Report back (see `references/connector-reference.md` for the
   save-draft-then-review-then-submit discipline this implies for any
   create/update).

10. **Advisory-first, always — the extra layer beyond the other
   persona skills, enforced in code, not just prompt.** Every
   write-capable capability here stages a draft and shows the user the
   exact payload — including which fields came from confirmed live
   metadata vs. which are inferred from their request — before doing
   anything else, **regardless of `qkeee_erp.mode`**. Concretely: run
   `python scripts/render_draft.py <input.json>` (doctype, action,
   payload, `confirmed_fields`/`inferred_fields`, `requested_by`) to
   produce the draft and a `confirmation_token`/`issued_at` pair, show it
   to the user, get their explicit go-ahead, then call
   `erp_client.gated_mutate_resource(..., confirmation_token=...,
   issued_at=...)` — **not** `mutate_resource()` directly. The gated
   function recomputes the token from the actual call's own arguments and
   refuses to proceed on a missing, stale, or mismatched token, so this
   isn't just a "please remember to confirm first" instruction — a caller
   that skips `render_draft.py` cannot produce a token that will pass.
   The eight named personas can call `mutate_resource()` more directly in
   read-write mode because their capability tables were hand-reviewed at
   design time; nothing here has had that review, since the doctype
   wasn't known in advance. If a specific catch-all capability ends up
   trusted and repeated, that's a signal it deserves to graduate into a
   proper persona skill, not a reason to loosen this skill's own default.

11. **Route every ERPNext call through `scripts/erp_client.py` (reads/
   writes — `gated_mutate_resource()` for writes, see above) or
   `scripts/discover.py` (metadata/app discovery)** — don't hand-roll
   HTTP calls elsewhere in this skill's logic.

## Capabilities

| Capability | How | Notes |
| --- | --- | --- |
| Coverage check / routing | Consult the routing table in `references/domain-knowledge.md` | Defer to a named persona skill when the request clearly fits one |
| Installed-app discovery | `discover.py modules` (primary, plain REST); `discover.py apps` (opportunistic, for version numbers) | `apps`'s RPC method is confirmed blocked on at least one real instance (`PermissionError: not whitelisted`) — try `modules` first, `apps` as a bonus, ask the user to paste Help → About only if exact versions matter and both fail |
| DocType → module → app resolution | `discover.py resolve <DocType>` | The core "which app owns this" lookup — run before assuming any doctype is uncovered custom territory. `app: null` with a non-null `app_lookup_error` means the lookup failed, not that there's confirmed no owning app — don't conflate the two |
| Live field-schema fetch | `discover.py meta <DocType>` | Fieldname/label/fieldtype/reqd/options as they exist on this instance right now — authoritative over any doc. Requires System Manager read access to DocType; a 403 here is a bot-permissions gap to flag, not a sign the doctype is missing |
| Knowledge-base lookup | Read `references/knowledge-base/<env-tag>/<app>.md` | Check before re-researching an app already investigated in a prior session |
| Knowledge-base research + write-up | GitHub README/docs fetch + `references/knowledge-base/<env-tag>/<app>.md` write, per the template in the KB README | Cross-check against live metadata; flag discrepancies, don't silently prefer one source |
| Generic resource query / get | `erp_client.py query\|get` (own copy) | Read-only, always allowed — same connector every other persona skill uses |
| Advisory draft rendering | `render_draft.py <input.json>` | Produces the draft + `confirmation_token`/`issued_at` — always the step before any write, see step 8 |
| Generic resource mutate | `erp_client.py mutate` → `gated_mutate_resource()` (own copy) | Gated by `qkeee_erp.mode` + `QKEEE_ERP_<TAG>_REQUESTED_BY` in code (inherited from `mutate_resource()`), **plus** a `confirmation_token`/`issued_at` from `render_draft.py` required in code — unconditionally, regardless of mode (see step 8); no separate un-gated mutate path exists in this skill's copy |

## Files

- `scripts/erp_client.py` — synced copy of `qkeee-erp-core`'s canonical
  connector (`SKILL_LABEL = "qkeee-erp-catch-all"` for audit-comment
  traceability), plus this skill's own `gated_mutate_resource()` write
  entry point (see step 8).
- `scripts/discover.py` — this skill's own addition: installed-app
  discovery, DocType → module → app resolution, live field-schema fetch.
  Not part of the canonical connector; specific to catch-all's
  investigation workflow.
- `scripts/confirm_token.py` / `scripts/render_draft.py` — this skill's
  own addition: the advisory-first draft-and-confirmation-token mechanism
  `gated_mutate_resource()` requires for every write. Not part of the
  canonical connector (the named persona skills don't need this — their
  capability tables were reviewed at design time); modeled on
  `qkeee-erp-system-admin`/`qkeee-erp-fixed-asset-manager`'s narrower
  per-capability `confirm_token.py`, applied here unconditionally to
  every write instead of just the highest-risk ones.
- `references/connector-reference.md` — synced copy of the canonical
  connector reference (endpoints, auth, the read-only/requester-
  attribution/save-draft-review-submit/audit-trail mechanics).
- `references/domain-knowledge.md` — the routing table, the SME
  investigation method, and the advisory-first extra-caution rule.
- `references/knowledge-base/` — per-environment-tag, per-app research
  notes this skill accumulates over sessions. See its `README.md` for the
  file convention. Non-secret only (no URLs/credentials — those stay in
  env vars per the module plan).

## Relationships

Consumes `qkeee-erp-core`'s connector (own synced copy). Defers to the
eight named persona skills whenever a request fits their coverage (see
routing table). If a catch-all-researched capability becomes trusted and
repeatedly used, consider promoting it into a new or existing persona
skill rather than growing this one into a tenth monolith.
