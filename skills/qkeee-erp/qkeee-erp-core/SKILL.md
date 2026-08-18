---
name: qkeee-erp-core
description: "Canonical ERPNext (Frappe REST API) connector — environment/tag management, auth, generic read/write primitives, and the read-only/read-write safety gate. Infrastructure skill: every qkeee-erp-* persona skill ships its own copy of this connector. Also usable standalone for ad hoc ERPNext queries with no persona framing. Use when the user wants to configure an ERPNext environment, run a raw ERPNext query, or check ERPNext connectivity outside of a specific persona (HR/Accounts/Procurement/etc)."
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
        prompt: "API key for this environment — generate this against a dedicated ERPNext integration/bot user, never against an individual's personal login (see Bot account below)"
      - name: "QKEEE_ERP_DEFAULT_API_SECRET"
        prompt: "API secret for this environment"
---

# qkeee-erp-core

Technical/infrastructure skill. Not a persona — the connector substrate
every `qkeee-erp-*` persona skill (HR Associate, Accounts Executive, Fixed
Asset Manager, System Admin, Procurement, Sales, Inventory, MIS Analyst)
copies its `erp_client.py` + connector reference from. Also directly
usable for ad hoc ERPNext queries with no persona framing.

## The non-negotiable

**Never issue a write call (create/update/submit/cancel/delete) while
`qkeee_erp.mode` is `read-only`.** This is enforced in `scripts/erp_client.py`
(`mutate_resource()` checks `mode` before every write), not just instructed
in this prompt.

**Never issue a write call without a requester identity.** All reads/writes
authenticate as one shared ERPNext bot/service account (see below) — without
a requester resolved from `QKEEE_ERP_<TAG>_REQUESTED_BY` (or a `--requested-by`
override), ERPNext's own audit trail would show only the bot, never who
actually asked. `mutate_resource()` refuses every write missing it, same as
the read-only gate.

## Bot account — mandatory

The API key/secret configured above must be generated against a dedicated
ERPNext integration/bot user (e.g. `qkeee-erp-bot@<org>`), **never** against
an individual staff member's personal login. If the bot key is provisioned
under a real person's account, every write in ERPNext attributes to that
person regardless of who actually requested it in chat — defeating the
requester-attribution mechanism below. Tell the user this explicitly if
they're setting up credentials for the first time.

**Proactively check this, don't just wait to be asked.** This applies even when `qkeee-erp-bot-init` itself hasn't
been invoked this session. If a `health` check reports `logged_in_as` an
identity that looks like a real staff member rather than a service
account, or the user is configuring `QKEEE_ERP_*` credentials for the
first time and hasn't mentioned a dedicated bot user, or a write fails/
behaves oddly around the `Qkeee Bot Audit Log` doctype (a sign
`qkeee-erp-bot-init` hasn't been run on this target yet): say so, and
suggest running `qkeee-erp-bot-init` — it can detect or create the
dedicated bot user (via an elevated admin login, distinct from this
skill's own steady-state bot credentials) and provisions the audit-trail
doctypes in the same pass. This is a recommendation, not a blocker —
don't refuse the user's actual request over it. Every persona skill
carries the same instruction in its own "Bot account" section.

## Requester attribution — mandatory on every write

Requester identity is sourced per-tag from `QKEEE_ERP_<TAG>_REQUESTED_BY`
in this profile's `.env` — not a global config value, so it switches
automatically with `--tag`/`qkeee_erp.active_env`. Before the first write
of a session, confirm this resolved value with the user (or ask, if the
env var isn't set for this tag) — re-confirm it same as the
active-environment reminder on long gaps or before a new batch of writes.
A `--requested-by` CLI flag is available as a one-off override for a
single call, never a substitute for setting the env var. Pass the
resolved value through to every `mutate` call. On success, `erp_client.py`
posts a best-effort Comment
on the affected record: `[qkeee-erp-core] <action> — requested by
<requested_by>, applied via qkeee-erp bot.` A comment failure never blocks
or rolls back the underlying write. Mention in your report-back that the
audit comment was posted.

## Audit trail — Qkeee Bot doctypes

Every `mutate_resource()` write is now also logged to the `Qkeee Bot Audit
Log` doctype (provisioned by `qkeee-erp-bot-init`), two-phase: an
`Attempted` row is inserted before the real write, updated to `Success`/
`Failure` after — an orphaned `Attempted` row is the detectable trace of a
crash mid-write. **This is best-effort, not a gate**, same posture as the
audit Comment above: if the target instance hasn't run `qkeee-erp-bot-init`
yet, or the audit doctypes are unreachable for any reason, the real write
still proceeds — logging failure never blocks or fails a user's requested
action. `AUDIT_EXEMPT_DOCTYPES` in `erp_client.py` prevents the logger from
recursively logging itself.

**Pass `user_approved=True` to `mutate_resource()` only when this write's
confirm stage actually ran with the user first.** This is a detection
field for later scanning of the bot's own behavior (did every write really
get confirmed, per the six-stage workflow pattern), not a second gate —
omitting it logs `"Not Confirmed"` rather than blocking the write, so a
skipped-confirmation bug becomes visible on an Audit Log scan instead of
being silently prevented or silently defaulted to looking fine.

**Read logging is opt-in via `debug=True`** on `query_resource()`/
`get_resource()` (CLI: `--debug`), sourced per-tag from
`QKEEE_ERP_<TAG>_DEBUG` in this profile's `.env` — not a global config
value, so debug can be on for one tag (e.g. a demo/qa environment) and
off for another (e.g. prod) in the same profile. Off by default — a
read-heavy persona could otherwise make Read rows the single biggest
volume source in the audit trail, defeating the point of gating anything
for bloat at all. A `--debug` CLI flag forces it on for a single call
only; it never turns debug off (there's no equivalent override to force
it off for one call when the tag has it on). Writes are audited
unconditionally regardless of this flag.

**Session/Message logging (`open_session()`/`log_message()`/
`close_session()`) is fully opt-in per caller**, not wired into
`mutate_resource()`/`query_resource()` automatically — call these
explicitly, gated on the same per-tag `QKEEE_ERP_<TAG>_DEBUG`, if this
skill's SKILL.md adopts full conversation logging. Every write-capable
persona skill now calls these when the active tag's debug default is
true. Full schema, the two-phase mechanism, and the debug-mode
volume-gating rationale: `qkeee-erp-bot-init/references/bot-doctypes-design.md`.

**A "success" from `register-persona`/`open-session` does not mean the
row actually landed — check the returned status, don't assume.** Every
best-effort write into the `Qkeee Bot *` doctypes above swallows its own
`ConnectorError` by design (a target instance that hasn't run
`qkeee-erp-bot-init` yet must never block a user's real request), but
that means the CLI exiting 0 is not proof the row was written:
`register-persona` returns `{"status": "created"|"already_registered"|"failed"}`
— `"failed"` means the `Qkeee Bot Persona` row was NOT created, almost
always because the doctype isn't provisioned on this instance yet.
`open-session` always prints a `session_id`, but on failure it's a
locally-generated `local-<timestamp>` fallback rather than a real Frappe
record name — a session/message logged under a `local-` id was never
actually persisted to ERPNext. Either signal should be treated the same
as a `logged_in_as` that looks like a personal account (see "Bot account"
above): proactively mention it once per session and suggest
`qkeee-erp-bot-init`, never silently ignore it and never let it block the
user's actual request.

## What you must do when invoked

1. **State the active environment before any read or write.** At the start
   of the session, report which tag + base URL this skill is connected to
   (e.g. "Connected to `qa` (`https://org-qa.erpnext.com`)"). Re-surface
   this reminder when picking work back up after a gap, or before a batch
   of write actions — never go silent about which environment is live.
2. **Resolve config from environment variables, not memory.** At install
   time only `QKEEE_ERP_DEFAULT_BASE_URL` / `_API_KEY` / `_API_SECRET` are
   prompted for (tag `DEFAULT`). Adding a second/third environment is a
   runtime action, not a reinstall: walk the user through naming a new tag
   and setting `QKEEE_ERP_<NEWTAG>_BASE_URL` / `_API_KEY` / `_API_SECRET`
   in **this agent profile's own `.env` file**
   (`.hermes/profile/<profile-name>/.env` — substitute the real profile
   name, never a repo-root or cross-profile `.hermes/.env`), then offer to
   switch `qkeee_erp.active_env` to it. This skill cannot declare those var
   names ahead of time since the tag is user-chosen. `<TAG>` is always the
   sanitized, uppercased form of the active tag. One profile's `.env` can
   hold multiple tags' vars at once — adding an environment means appending
   three more lines to that same file, not creating a new file or profile.
   If any of the three vars for the active tag are missing, tell the user
   exactly which variable is missing — never a generic "auth failed." Two
   more vars are OPTIONAL per tag, same `.env` file: `QKEEE_ERP_<TAG>_DEBUG`
   (defaults false) and `QKEEE_ERP_<TAG>_REQUESTED_BY` (no default — a
   write on a tag without this set needs `--requested-by` passed explicitly
   or the user asked). Full rationale: `references/connector-reference.md`'s
   "Environment / tag
   model" section.
3. **Health check on first real use.** Before the first query/mutate of a
   session, run a connectivity check (`python scripts/erp_client.py --tag
   <tag> health`) and surface a clear error if the URL/credentials are
   wrong, rather than letting a raw HTTP error leak through. `--tag` is
   required by the CLI for health/query/mutate, and `--mode` is required
   for mutate — neither falls back to an ambient shell variable, so a
   stray `QKEEE_ERP_MODE` left over in someone's shell profile can never
   silently override what `qkeee_erp.mode` actually says.
4. **Route every ERPNext call through `scripts/erp_client.py`.** Don't
   hand-roll HTTP calls elsewhere in this skill's logic — the script is
   the single place auth, env resolution, and the read-only gate are
   enforced.
5. **Prefer a harness-native HTTP-capable tool if one is discoverable.**
   If the host harness exposes a way to enumerate installed tools/skills
   and one already does authenticated HTTP well, prefer it over shelling
   out to this script. If discovery isn't supported in this harness,
   degrade gracefully to `erp_client.py` — never hard-fail over that.
6. **Only the active-environment tag name (not URL/credentials) may be
   remembered across sessions**, so a reminder like "last used: `qa`" can
   be given at the start of a new session. Credentials and URLs never go
   into agent-curated memory — they live only in environment variables.
7. **Any interim/scratch file goes under `terminal.cwd`, never `/tmp`.**
   A JSON payload assembled for `render_report.py`/`render_*_draft.py`, a
   staged attachment, or any other file that isn't the final deliverable
   is written under `terminal.cwd` from `config.yaml` — never `/tmp` or
   another ad hoc path, which isn't guaranteed to be the same filesystem,
   isn't scoped to this profile, and isn't guaranteed to persist for the
   session. See `references/connector-reference.md`'s "Interim / scratch
   files" section. Clean scratch files up once the task no longer needs
   them.
8. **Save as draft, review, then submit — never create-and-submit in one
   motion.** `create`/`update` and `submit` are always
   separate `mutate` calls (see the Generic resource mutate capability
   below); this skill's job is to keep them separate, not chain them. When
   a persona skill (or an ad hoc caller) is driving a create/update
   followed by a submit, re-fetch the record by its returned `name` —
   use `erp_client.py get <DocType> <name>`, not `query --filters`, since
   `query` silently drops child-table data even when named in `--fields`
   (confirmed live; see Capabilities table) — and review every persisted
   field, including that every Link-type field resolves to a real,
   existing record, before issuing the `submit` call.
   See `references/connector-reference.md`'s "Save-draft-then-review-then-
   submit discipline" section for the full rationale; no change was needed
   in `erp_client.py` itself for this, since `create`/`update` already
   leave a record at `docstatus 0` and `submit` was already a distinct
   action.

## Capabilities

| Capability | How | Notes |
| --- | --- | --- |
| Environment configuration — add/switch | Walk the user through naming a tag and setting its 3 env vars in their shell; update `qkeee_erp.active_env` | Runtime action, not a reinstall — multiple tags can coexist, only one active |
| Environment configuration — list | `erp_client.py list-envs` | Lists tags with all 3 vars present in the current shell env; can't discover tags configured in a shell this process doesn't inherit from |
| Generic resource query | `erp_client.py query <DocType> --filters ... --fields ... [--debug]` | Read-only, always allowed. Response includes `has_more` — if true, narrow filters or raise `--limit` rather than assuming the result set is complete. **Does not return child-table (Table-field) data** — Frappe's list endpoint silently drops it even when named in `--fields`, confirmed live against `<erp-instance>`. Prefer this over `get` whenever child-table data isn't needed — ~25x cheaper (336 bytes vs 8,378 bytes measured on a Sales Order status read). `--debug` forces this read to log to Qkeee Bot Audit Log for this call — normally sourced from `QKEEE_ERP_<TAG>_DEBUG` |
| Single-resource full-doc fetch | `erp_client.py get <DocType> <name> [--debug]` | The only way to get child-table rows (needed for Link-field validity review before a submit). Frappe's single-resource GET ignores `--fields` and always returns everything, so this noise-strips audit metadata + presentation-only HTML fields by default (~38% smaller, confirmed live; `--no-strip` for the raw doc) — never strips Link fields or child tables. `--debug` same as query above |
| Generic resource mutate | `erp_client.py mutate <DocType> <create\|update\|submit\|cancel\|delete> [--requested-by <id>] [--user-approved] [--approval-note ...]` | Gated by `qkeee_erp.mode` and a resolved requester (`QKEEE_ERP_<TAG>_REQUESTED_BY`, or `--requested-by` to override for this call); refuses in read-only or with no requester either way. Posts a best-effort audit Comment naming the requester on success, and always logs a two-phase Attempted→Success/Failure row to Qkeee Bot Audit Log (best-effort, never blocks the write — see Audit trail section above). `--user-approved` should only be passed when this write's confirm stage genuinely ran with the user; it's a scan-for-violations field, not a second gate. `create`/`update` and `submit` are separate calls — always review the re-fetched draft (`get`, not `query`, when Link-field validity inside a child table needs checking) between them, never chain create/update straight into submit |
| Connectivity health check | `erp_client.py health` | Run before first read/write of a session |
| Harness capability discovery | Ask the harness (if it exposes tool listing) whether a native HTTP/report tool already exists | Applies to this skill and is the general pattern other qkeee-erp-* skills should follow too |

## Files

- `scripts/erp_client.py` — the connector implementation (stdlib-only
  Python; no third-party deps so persona skills can copy it verbatim).
  Includes the Qkeee Bot audit-trail retrofit:
  `record_audit_log_start()`/`record_audit_log_finish()` (two-phase write
  logging, wired into `mutate_resource()`), debug-gated read logging in
  `query_resource()`/`get_resource()`, and opt-in `open_session()`/
  `log_message()`/`close_session()` for full conversation logging.
- `references/connector-reference.md` — endpoint table, auth details,
  env/tag model, and the source-of-truth doc for anyone syncing a persona
  skill's connector copy from this canonical one.
- `qkeee-erp-bot-init/references/bot-doctypes-design.md` (sibling skill)
  — the audit-trail schema and decision log this retrofit implements.

## Extension point

To target a different ERP backend, replace `scripts/erp_client.py` and
`references/connector-reference.md` (here and in every persona skill's own
copy). Nothing else in the library — domain-knowledge.md files, persona
SKILL.md instructions — needs to change; they're written to be ERP-agnostic
in substance.

## Relationships

Source of truth for the connector copy embedded in every other
`qkeee-erp-*` skill. Build this skill first; sync its `scripts/` and
`references/connector-reference.md` into each persona skill as they're
built.
