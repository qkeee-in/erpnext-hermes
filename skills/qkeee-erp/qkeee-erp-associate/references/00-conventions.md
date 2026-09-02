# Conventions and non-negotiables

Single copy, referenced by every `domains/*.md` file rather than restated
in each — this is where naming rules, the GRC baseline, and the
non-negotiables live once. If a domain file's own guardrails section
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

## Naming conventions

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
| Task spec | `./qkeee-erp-specs/<slug>-<YYYYMMDD-HHMM>.md`, relative to the session's actual working directory (`terminal.cwd` on gateway/cron, launch dir on local CLI) | see `references/03-spec-driven-execution.md`; `<profile>/workspace/...` only as a last-resort fallback when neither resolves; disposable across sessions, not mid-task |
| New-learning entries | append under `## Learned <YYYY-MM-DD>` | never edit or delete a prior entry |

**Before any `skill_manage(create)` touching ERPNext/organizational
content — including Hermes' own autonomous "offer to save as skill"
reflex after a hard session, not just `SKILL.md`'s structured
environment-promotion flow (activation step 2) — check for existing
coverage first:**

1. Is this already documented in this skill's own `references/` tree
   (this file, `01-connectivity.md` through `04-erp-doc-lookup.md`, or a
   `domains/*.md` file)? If yes, don't create a new skill — the finding
   belongs nowhere else. Patch the associate's own reference file only
   through a foreground, user-directed edit (see the GRC baseline's
   "Shipped skill is protected from autonomous drift" entry — this
   skill's own tree is not something a background/autonomous save should
   touch).
2. Is this instance/environment-specific learning (a custom doctype, a
   version quirk, an RBAC finding for one tag)? That's `qkeee-erp-learned/
   <env-tag>` territory per the table above — `category='qkeee-erp-
   learned'`, name=`<env-tag>` (or a nested `references/` file under an
   existing `qkeee-erp-learned/<env-tag>` skill via `write_file`/`patch`,
   preferred over a brand-new top-level skill for a tag that already has
   one). Never a freeform category (`erpnext`, `erp`, or similar) — the
   `category` param on `skill_manage` is unvalidated free text and Hermes
   enforces nothing about it; this convention is the only thing that does.
3. Only create a genuinely new, unrelated skill when neither 1 nor 2
   applies. Restating this file's non-negotiables or the GRC baseline in
   different words is never grounds for a new skill — extend or link
   back to this file instead.

`<profile>` = the active Hermes profile root (`~/.hermes/` by default,
`~/.hermes/profiles/<name>/` for a named one) — resolved through Hermes'
own profile mechanism, never hardcoded or invented by this skill. The
task-spec row above is the one exception that does target the working
directory directly: local CLI ignores the `terminal.cwd` *config key*
but still writes relative to the real launch directory, which a spec
uses on purpose — see `01-connectivity.md` and
`references/03-spec-driven-execution.md`.

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

These hold across every domain. Each is enforced in `scripts/core/client.py`,
not left to this document alone — consult that module's own docstrings
for the exact mechanism.

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
   Every `scripts/domains/<slug>.py` module declares this tuple and
   registers it via `core.client.register_domain_allowlist()`.
   `mutate_resource(..., domain=<slug>)` raises `DoctypeNotAllowedError`
   for any doctype outside it, or for an unregistered/unknown domain
   name — a typo'd domain fails closed, it does not silently skip the
   check. `domains/mis.py` registers an empty tuple, so MIS can never
   write (see `domains/mis.md`).
4. **Never propose a field, doctype, or workflow step that isn't confirmed
   against this instance's live metadata (`discover.py`) or an explicit
   statement from the user.** Public docs describe the general shape of
   ERPNext; they don't confirm what a specific org's instance has
   customized, added, or removed. An honest "I don't see that field on
   this DocType" beats a guessed field name that happens to resolve.
5. **Save-draft-then-review-then-submit, always three distinct steps —
   code-enforced, not just sequencing discipline.** `create`/`update` and
   `submit` are always separate `mutate` calls. Re-fetch the record by its
   returned `name` after create/update and review every persisted field —
   in particular that every Link-type field resolves to a real, existing
   record — before issuing a `submit`. Use `core.client.get_resource()`
   (or the domain's own equivalent) when the review needs child-table data
   (Frappe's list endpoint silently drops child tables even when named in
   `fields`); `query_resource()` with explicit `fields` is far cheaper
   when it doesn't. `submit`/`cancel` on every domain
   (accounts/hr-payroll/sales/procurement/inventory) additionally require
   a fresh `confirmation_token` — `mutate_resource()` refuses either
   action without one that matches the exact (action, doctype, name,
   payload, requested_by, issued_at) facts, computed via
   `scripts/core/confirm_token.py`'s `advisory-token` CLI over what was
   actually shown to and confirmed by the user. Fixed-assets and
   system-admin instead carry their own stricter, capability-specific
   token schemes for their highest-blast-radius actions (see those
   domains' own modules) — this generic gate is what backstops the rest.
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

- **Source the requester identity from the channel's own authenticated
  sender field, never reconstruct it conversationally.** Hermes' gateway
  already resolves and authorizes the inbound sender before this skill
  ever sees the message (platform allowlists, DM pairing — see
  [Security | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/security)'s
  "User Authorization" section). Use that already-resolved platform
  identity (the Google Chat/Discord user id, or the email `From` header)
  as the input to the ERPNext-`User` lookup below — don't ask the model to
  infer or restate who's asking from conversation text, and never accept a
  `requested_by` that didn't originate from that channel-provided field.
  This is what `resource_exists(tag, "User", requested_by)` and
  `check_user_permission()` are validating *against*; they can't detect a
  plausible-looking but fabricated identity that was never actually tied
  to the channel message.
- **RBAC pre-check, every environment.** The associate runs the same
  requester-permission check on every environment, every fetch or
  write, not PROD only: resolve the requester as a real ERPNext `User`,
  then confirm via ERPNext's own `frappe.client.has_permission` that they
  actually hold the permission the call needs. Presence of `requested_by`
  is mandatory on PROD only (`_is_prod_tag()` — a tag whose name matches
  `/prod/i`, e.g. `PROD_ERP`, `client-a-prod`); whenever one IS supplied,
  on any tag, it's validated. Function/constant names
  (`_validate_prod_requester()`, `PROD_GATE_EXEMPT_DOCTYPES`) reflect a
  narrower PROD-only origin — don't read the name as scope.
- **Read audit logging, always on.** Every access gets an audit row in
  `Qkeee Bot Audit Log`, reads included, unconditionally — there is no
  debug flag gating this in `core/client.py`.
- **PII/GDPR redaction, single source.** `core.client.redact_pii()` is
  the one place sensitive fields get scrubbed before display, storage, or
  logging. Never re-implemented per domain.
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
- **`session_id` — regenerate per platform session, never carry forward
  indefinitely.** Live-observed: a Discord/Slack/etc conversation resumed
  across a long gap (a day, a context-compaction event) can end up handing
  the connector a `session_id` that's drifted stale or malformed — unlike
  `requested_by`/`reference_doctype`, this value is never validated
  anywhere upstream of the raw Audit Log insert, so a bad one silently
  drops the Audit Log row (real write is unaffected) with nothing but a
  stderr WARN to show for it. `core/client.py` now clamps/sanitizes
  `session`/`domain_code`/`channel` defensively before insert, but the
  caller-side fix is the one that actually matters: derive `session_id`
  fresh at the start of each logical session (new platform thread/DM,
  bot restart, or a context-compaction event mid-conversation — treat
  compaction as a new logical session, matching what "open a fresh
  Discord session" empirically fixes) rather than reusing/appending to
  one carried across the whole lifetime of a long-running chat. After any
  write, check the returned `_audit_log_status` key (`"ok"`/`"exempt"` are
  healthy; `"insert_failed"`/`"update_failed"` mean this write did NOT
  make it into the audit trail) and surface a warning to the user rather
  than silently trusting the best-effort insert.
- **Bot account — mandatory, dedicated service identity, and never
  privileged.** The API key/secret every domain authenticates with must
  be generated against a dedicated ERPNext integration/bot user (e.g.
  `qkeee-erp-bot@<org>`), never an individual's personal login — otherwise
  every write attributes to that person regardless of who actually
  requested it, defeating requester attribution. **That bot user must also
  never be `Administrator` and must never hold `System Manager`** (or any
  other role granting a blanket Desk permission bypass) — live-confirmed:
  under a privileged identity, ERPNext's `frappe.client.has_permission`
  doesn't reliably discriminate by the `user=` param it's given, which
  makes the RBAC pre-check below a no-op that silently rubber-stamps any
  `requested_by`. This isn't instance-specific — stock Frappe's
  `frappe.client.has_permission` has no `user=` parameter at all; it only
  ever answers for the calling session. `core/client.py` enforces the
  consequence in code: a live probe (`verify_rbac_precheck_reliable()`)
  runs per tag and, when the bot identity is privileged or the probe
  shows the check doesn't discriminate, a write proceeds on a warning
  only if it has one of two design-time-reviewed controls ahead of it —
  a **domain-scoped write** (`domain=` set, doctype already reviewed
  into that domain's `ALLOWED_WRITE_DOCTYPES`, +confirmation-token where
  registered), or a **`gated_mutate_resource()` write with a verified
  advisory-draft token** (covers a doctype no domain owns, e.g. Company —
  the mandatory draft-then-confirm flow is the reviewed control there
  instead of an allowlist). A write with **neither** — no `domain=` and
  no verified token — is refused with `PrivilegedBotAccountError` until
  the bot account is fixed; that's the true "nothing reviewed this"
  case. Either way this is a blocker/warning enforced in code, not a
  courtesy, and is a **different** failure mode than the "not a personal
  login" check below, which is only a recommendation. Check both
  proactively: if a
  `health` check's `logged_in_as` looks like a real staff member, or its
  `rbac_precheck_reliable` field is `false`, or the user is configuring
  credentials for the first time without mentioning a dedicated bot user,
  or a write behaves oddly around `Qkeee Bot Audit Log` (a sign bot-init
  hasn't run on this target) — say so and suggest `init_bot.py` (see
  `references/domains/system-admin.md` and `scripts/init_bot.py`).
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
  that evolution. Mechanically this is a Hermes profile config
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
- **Doc claims about skill-write gating are not self-enforcing — verify
  `config.yaml` matches them.** `profile.md` states local skill writes are
  gated by `skills.write_approval` and reviewed before landing; that's
  only true if `skills.write_approval: true` is actually set in
  `config.yaml` — the key defaults off (`tools/write_approval.py`), and a
  profile can drift into having the doc claim without the config backing
  it (this happened: see the check-before-create rule above). Same for
  `curator.consolidate` (default off, `agent/curator.py`) — the
  mechanism that would otherwise merge overlapping agent-created skills
  back into this one doesn't run unless explicitly turned on. Neither is
  this skill's own code to enforce; flag a mismatch to the operator if
  ever discovered, same as the `external_dirs` check above.
