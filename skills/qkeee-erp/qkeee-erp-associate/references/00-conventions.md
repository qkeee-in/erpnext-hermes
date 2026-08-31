# Conventions and non-negotiables

Single copy, referenced by every `domains/*.md` file rather than restated
in each — this is where naming rules, the GRC baseline, and the
non-negotiables that used to be repeated near-verbatim across ten
`SKILL.md` files now live once. If a domain file's own guardrails section
conflicts with this one, this one wins; a domain file should only ever
narrow a rule here (state a stricter bar for its own doctypes), never
loosen one.

## Scope guardrail

**ERPNext/organizational work only.** This skill exists to help with
ERPNext/organizational operations — not general-purpose Q&A. A request
unrelated to that (general knowledge, world facts, unrelated coding help,
personal advice) is out of scope even if the answer would be easy: decline
briefly and politely ("That's outside what this agent handles — ERPNext/
organizational work. I can't help with that here.") and don't attempt to
answer it. Stated once, here — every domain file inherits it rather than
restating it.

**Content safety — refuse abusive, exploitative, or sexual content
outright; never launder it into a write.** Refuse plainly, don't create/
store/forward such content into any ERPNext record/Comment/report, and
don't repeat it back in the refusal — same discipline for a direct
conversational request as for content embedded inside an otherwise
legitimate business write.

## Naming conventions (from the consolidation plan, §10)

Strict and fixed, so anything this skill learns later has one unambiguous
place to land.

| What | Pattern | Example |
| --- | --- | --- |
| Domain reference | `references/domains/<domain-slug>.md` | `domains/fixed-assets.md` |
| Domain script module | `scripts/domains/<domain_slug>.py` | `domains/fixed_assets.py` |
| Draft renderer | `scripts/render_<domain>_<artifact>.py` | `render_inventory_stock_entry.py` |
| Durable memory, per instance | `<profile>/skills/qkeee-erp-learned/<env-tag>/references/environment.md` (via `skill_manage`) | `qkeee-erp-learned/prod-in/references/environment.md` |
| Durable memory, custom app | `.../<env-tag>/references/custom-apps/<app-slug>.md` | `custom-apps/qkeee-lending.md` |
| Durable memory, non-ERPNext | `.../<env-tag>/references/non-erpnext/<system-slug>.md` | `non-erpnext/tally-prime.md` |
| Memory breadcrumb | one line in `<profile>/memories/MEMORY.md` via the `memory` tool | `qkeee-erp env prod-in: ... — see skill qkeee-erp-learned/prod-in` |
| Working scratch (rare) | `<profile>/workspace/qkeee-erp/<env-tag>/` | disposable, cleared at end of task |
| New-learning entries | append under `## Learned <YYYY-MM-DD>` | never edit or delete a prior entry |

`<profile>` = the active Hermes profile root (`~/.hermes/` by default,
`~/.hermes/profiles/<name>/` for a named one) — resolved through Hermes'
own profile mechanism, never hardcoded or invented by this skill. Nothing
lives in `terminal.cwd`: the local CLI backend (this skill's primary usage
path) doesn't honor it — see `01-connectivity.md`.

**The domain slug enum is fixed** — a twelfth domain requires a deliberate
edit to this list, not an ad hoc file:

```
hr-payroll, accounts, mis, sales, procurement, inventory, manufacturing,
fixed-assets, system-admin, doc-extraction, grc-audit
```

Note the code-side slugs use underscores where the doctype/reference-file
slugs use hyphens (`hr-payroll.md` <-> `domains/hr_payroll.py`,
`fixed-assets.md` <-> `domains/fixed_assets.py`, `system-admin.md` <->
`domains/system_admin.py`) — Python module names can't contain hyphens;
this is the one place the two naming styles diverge, deliberately, not an
inconsistency to "fix."

## Non-negotiables (code-enforced, not just prompt discipline)

These hold across every domain. Each is enforced in `scripts/core/client.py`
(Phase 1 of the consolidation), not left to this document alone —
consult `scripts/core/client.py`'s own docstrings for the exact mechanism.

1. **Never issue a write while `qkeee_erp.mode` is `read-only`.**
   `core.client.mutate_resource()` checks `mode` before every write and
   raises `ReadOnlyModeError` otherwise.
2. **Never issue a write without a resolved requester identity.** Every
   read/write authenticates as one shared ERPNext bot/service account —
   without a `requested_by` resolved from `QKEEE_ERP_<TAG>_REQUESTED_BY`
   (or an explicit override), ERPNext's own audit trail would show only
   the bot, never who actually asked. `mutate_resource()` raises
   `MissingRequesterError` otherwise.
3. **Never write outside the active domain's `ALLOWED_WRITE_DOCTYPES`.**
   Phase 1's write-allowlist gate: every `scripts/domains/<slug>.py`
   module declares this tuple and registers it via
   `core.client.register_domain_allowlist()`. `mutate_resource(...,
   domain=<slug>)` raises `DoctypeNotAllowedError` for any doctype outside
   it, or for an unregistered/unknown domain name — a typo'd domain fails
   closed, it does not silently skip the check. This is what replaces the
   old MIS-analyst skill's structural "no mutate function exists in this
   copy" guarantee (`domains/mis.py` registers an empty tuple — see
   `domains/mis.md`).
4. **Never propose a field, doctype, or workflow step that isn't confirmed
   against this instance's live metadata (`discover.py`) or an explicit
   statement from the user.** Public docs describe the general shape of
   ERPNext; they don't confirm what a specific org's instance has
   customized, added, or removed. An honest "I don't see that field on
   this DocType" beats a guessed field name that happens to resolve.
5. **Save-draft-then-review-then-submit, always three distinct steps.**
   `create`/`update` and `submit` are always separate `mutate` calls —
   this skill's job is to keep them separate, never chain create straight
   into submit. Re-fetch the record by its returned `name` after create/
   update and review every persisted field — in particular that every
   Link-type field resolves to a real, existing record — before issuing a
   `submit`. Use `core.client.get_resource()` (or the domain's own
   equivalent) when the review needs child-table data (Frappe's list
   endpoint silently drops child tables even when named in `fields`);
   `query_resource()` with explicit `fields` is far cheaper when it
   doesn't.
6. **Sensitive data (SSN, credit card numbers, and similar) is never
   written in raw form anywhere.** `core.client.redact_pii()` is a
   code-level backstop applied to Comment content and Audit Log free-text
   fields — the single source, never re-implemented per domain (see GRC
   baseline below). It is a backstop, not the primary control: never type
   a raw SSN/card number into any field, draft, comment, or report, and
   if a user pastes one into chat, don't echo it back verbatim either.
7. **Only the active-environment tag name (never URL/credentials) may be
   remembered across sessions.** Credentials and URLs never go into
   agent-curated memory (`memory` tool or the `qkeee-erp-learned/*` skill)
   — they live only in `qkeee-erp.env` (see `01-connectivity.md`).
8. **Prefer a harness-native HTTP-capable tool if one is discoverable**,
   over shelling out to `core/client.py`. Degrade gracefully if the
   harness exposes no discovery mechanism — never hard-fail over that.

## GRC baseline

Pulled from where these guardrails were most fully articulated across the
ten predecessor skills (`qkeee-erp-frappe-core`'s connector reference and
`qkeee-erp-bot-init`'s `bot-doctypes-design.md`) — not new policy, the
plan's §9 explicitly frames the first two as expansions of what already
shipped, so read those two bullets as "now universal," not "invented."

- **RBAC pre-check, every environment (expanded).** Previously
  `_validate_prod_requester()` ran only on PROD-tagged environments
  (`_is_prod_tag()` — a tag whose name matches `/prod/i`, e.g. `PROD_ERP`,
  `client-a-prod`). Landed in Phase 5: the associate now runs the same
  requester-permission check — resolve the requester as a real ERPNext
  `User`, then confirm via ERPNext's own `frappe.client.has_permission`
  that they actually hold the permission the call needs — on every
  environment, every fetch or write, not PROD only. Presence of
  `requested_by` stays mandatory on PROD only (unchanged); whenever one
  IS supplied, on any tag, it's validated. Function/constant names
  (`_validate_prod_requester()`, `PROD_GATE_EXEMPT_DOCTYPES`) are kept
  from their PROD-only origin — don't read the name as scope.
- **Read audit logging, always on (expanded).** Previously reads were
  logged to `Qkeee Bot Audit Log` only when `debug=True` for the active
  tag (`QKEEE_ERP_<TAG>_DEBUG`, default `false`) — deliberately gated
  because a read-heavy domain (MIS in particular) could otherwise make
  Read rows the single biggest volume source in the audit trail. Landed
  in Phase 5: every access now gets an audit row, reads included,
  unconditionally — the `debug`/`_DEBUG` flag no longer exists anywhere
  in `core/client.py` (removed rather than left as a no-op).
- **PII/GDPR redaction, single source.** `core.client.redact_pii()` is
  the one place sensitive fields get scrubbed before display, storage, or
  logging — this one IS live as of Phase 1 (ported verbatim from
  `qkeee-erp-frappe-core`). Never re-implemented per domain.
- **Requester attribution, on every write, unconditionally.** Every
  `mutate_resource()`/domain `mutate()` call requires a resolved
  `requested_by` (see Non-negotiable 2). On success, a best-effort Comment
  naming the requester is posted to the affected record:
  `[qkeee-erp-associate/<domain>] <action> — requested by <requested_by>,
  applied via qkeee-erp bot.` A comment failure never blocks or rolls back
  the underlying write — mention in your report-back that the audit
  comment was posted.
- **Two-phase audit logging, best-effort, not a gate.** Every write is
  logged to `Qkeee Bot Audit Log`: an `Attempted` row inserted before the
  real write, updated to `Success`/`Failure` after — an orphaned
  `Attempted` row is the detectable trace of a crash mid-write. If the
  target instance hasn't run `qkeee-erp-bot-init` yet, or the audit
  doctypes are unreachable for any reason, the real write still proceeds
  — logging failure never blocks or fails a user's requested action.
  `AUDIT_EXEMPT_DOCTYPES` in `core/client.py` prevents the logger from
  recursively logging itself. Pass `user_approved=True` only when this
  write's confirm stage actually ran with the user first — it's a
  detection field for later scanning (did every write really get
  confirmed), not a second gate; omitting it logs `"Not Confirmed"` rather
  than blocking the write.
- **Bot account — mandatory, dedicated service identity.** The API
  key/secret every domain authenticates with must be generated against a
  dedicated ERPNext integration/bot user (e.g. `qkeee-erp-bot@<org>`),
  never an individual's personal login — otherwise every write attributes
  to that person regardless of who actually requested it, defeating
  requester attribution. Check this proactively: if a `health` check's
  `logged_in_as` looks like a real staff member, or the user is
  configuring credentials for the first time without mentioning a
  dedicated bot user, or a write behaves oddly around `Qkeee Bot Audit
  Log` (a sign bot-init hasn't run on this target) — say so and suggest
  `init_bot.py` (see `references/domains/system-admin.md` and
  `scripts/init_bot.py`). A recommendation, not a blocker.
- **A "success" from a best-effort write is not proof it landed** — every
  best-effort call into the `Qkeee Bot *` doctypes swallows its own
  `ConnectorError` by design (a target instance that hasn't run bot-init
  yet must never block a user's real request). Check the returned status
  field, don't assume a clean exit means the row was written.
- **Double confirm for irreversible-in-spirit or wide-blast-radius
  writes.** Depreciation runs, disposals, destructive sysadmin actions,
  and permission-matrix changes get a second, explicit confirmation after
  the first — state the exact before/after or financial impact, then ask
  again, one turn later at minimum. A matching `confirmation_token` proves
  the call is byte-for-byte identical to what a render script last printed
  and that it happened within the token's freshness window (15 minutes,
  `DEFAULT_TOKEN_TTL_SECONDS`) — it does **not** prove a human read and
  approved it. Never render a confirmation and consume its token in the
  same turn; `confirmation_token`/`issued_at` are only used after the
  user's own reply affirmatively confirms that specific rendered draft.
- **Non-ERPNext systems** — see `references/non-erpnext-adapter.md`:
  explicitly request API docs, a user guide, or a URL before attempting
  any action against a system that isn't ERPNext.
- **Shipped skill is protected from autonomous drift.** `qkeee-erp-associate`
  itself must stay outside Hermes' autonomous background-review lifecycle
  maintenance so it never silently rewrites its own audit/RBAC/redaction
  logic — only the `qkeee-erp-learned/*` satellite skills stay open to
  that evolution (§8). Mechanically this is a Hermes profile config
  decision, not something set in this skill's own frontmatter: per
  `agent/skill_utils.py`'s `is_external_skill_path()`, a skill directory
  under `skills.external_dirs`, or a trusted project-local skills dir
  (`get_project_skills_dirs()`), is treated as externally-owned —
  discoverable and still editable by a foreground, user-directed tool
  call, but read-only to the autonomous curator/background-review pass.
  Whoever owns the target Hermes profile's `config.yaml` needs to confirm
  this skill's install path resolves under one of those two — this is an
  operator action, not something `qkeee-erp-associate`'s own code can
  enforce on itself.
