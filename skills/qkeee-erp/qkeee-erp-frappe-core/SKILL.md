---
name: qkeee-erp-frappe-core
description: "Canonical ERPNext (Frappe REST API) connector — environment/tag management, auth, generic read/write primitives, and the read-only/read-write safety gate. Infrastructure skill: every qkeee-erp-* persona skill ships its own copy of this connector. Also this library's fallback-investigation skill (merged from the former qkeee-erp-catch-all) for whatever doesn't fit one of the eight named persona skills — companion Frappe apps (CRM, Helpdesk, LMS, Insights, ...) and org-specific custom doctypes — investigating the target instance's actual installed apps and live DocType metadata before proposing anything, and building a per-instance knowledge base over sessions. Use when the user wants to configure an ERPNext environment, run a raw ERPNext query, check ERPNext connectivity outside of a specific persona (HR/Accounts/Procurement/etc), or asks about a doctype/feature that doesn't belong to any named persona skill."
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

# qkeee-erp-frappe-core

Two identities in one skill:

1. **Technical/infrastructure substrate.** Not a persona itself in this
   role — every `qkeee-erp-*` persona skill (HR Associate, Accounts
   Executive, Fixed Asset Manager, System Admin, Procurement, Sales,
   Inventory, MIS Analyst) copies its `erp_client.py` + connector
   reference from here.
2. **Fallback-investigation persona** (merged in from the former
   `qkeee-erp-catch-all` skill, 2026-08-18) — the generic, self-improving
   skill for whatever the eight named persona skills don't cover:
   companion Frappe apps (CRM, Helpdesk, LMS, Insights, Wiki, Drive,
   Gameplan, Builder, Payments, ...) and org-specific custom doctypes.
   Also directly usable for ad hoc ERPNext queries with no persona
   framing. Where every named persona skill ships with a hand-researched
   `domain-knowledge.md` and a pre-vetted capability table, this identity
   has neither at install time — its job is to build that understanding
   live, from the target instance's actual metadata and installed-app
   inventory, and to remember what it learns per environment tag so the
   next session starts smarter than the last. See
   `references/domain-knowledge.md` for the routing table and
   investigation method.

## The non-negotiables

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

**Never propose a field, doctype, or workflow step that isn't confirmed
against this instance's live metadata (`scripts/discover.py`) or an
explicit statement from the user** — applies specifically to this
skill's fallback-investigation identity. GitHub READMEs and docs.frappe.io
describe the general shape of an app; they don't confirm what a specific
org's instance has customized, added, or removed. A guessed field name
that happens to resolve (e.g. a Link value that matches some unrelated
existing record) is a worse failure than an honest "I don't see that
field on this DocType."

**Every write this skill performs itself (fallback-investigation mode) is
advisory-first, unconditionally, enforced in code.** `scripts/erp_client.py`'s
`gated_mutate_resource()` — this skill's own write entry point, distinct
from the plain `mutate_resource()` every persona skill's copy uses — refuses
to proceed without a `confirmation_token`/`issued_at` from
`scripts/render_draft.py` that matches the exact call being made. Unlike
the eight named persona skills (whose capability tables were hand-reviewed
at design time, so they can call `mutate_resource()` directly in
read-write mode), nothing this skill investigates has had that review — see
"Advisory-first, always" under What you must do below.

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
on the affected record: `[qkeee-erp-frappe-core] <action> — requested by
<requested_by>, applied via qkeee-erp bot.` A comment failure never blocks
or rolls back the underlying write. Mention in your report-back that the
audit comment was posted.

## Audit trail — Qkeee Bot doctypes

Every `mutate_resource()`/`gated_mutate_resource()` write is also logged
to the `Qkeee Bot Audit Log` doctype (provisioned by `qkeee-erp-bot-init`),
two-phase: an
`Attempted` row is inserted before the real write, updated to `Success`/
`Failure` after — an orphaned `Attempted` row is the detectable trace of a
crash mid-write. **This is best-effort, not a gate**, same posture as the
audit Comment above: if the target instance hasn't run `qkeee-erp-bot-init`
yet, or the audit doctypes are unreachable for any reason, the real write
still proceeds — logging failure never blocks or fails a user's requested
action. `AUDIT_EXEMPT_DOCTYPES` in `erp_client.py` prevents the logger from
recursively logging itself.

**Pass `user_approved=True` to `mutate_resource()` only when this write's
confirm stage actually ran with the user first.** (`gated_mutate_resource()`
always passes this as `True` once its token check passes — there is no
unconfirmed write path through it.) This is a detection
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

Every `query`/`get`/`report`/`mutate` call accepts a `--session-id` —
it's a plain string correlator threaded into Audit Log's `session`
field, nothing more to set up. Full schema, the two-phase mechanism,
and the debug-mode volume-gating rationale for reads:
`qkeee-erp-bot-init/references/bot-doctypes-design.md`.

**A "success" from `register-persona` does not mean the row actually
landed — check the returned status, don't assume.** Every best-effort
write into the `Qkeee Bot *` doctypes above swallows its own
`ConnectorError` by design (a target instance that hasn't run
`qkeee-erp-bot-init` yet must never block a user's real request), but
that means the CLI exiting 0 is not proof the row was written:
`register-persona` returns `{"status": "created"|"already_registered"|"failed"}`
— `"failed"` means the `Qkeee Bot Persona` row was NOT created, almost
always because the doctype isn't provisioned on this instance yet. This
should be treated the same as a `logged_in_as` that looks like a
personal account (see "Bot account" above): proactively mention it once
per session and suggest `qkeee-erp-bot-init`, never silently ignore it
and never let it block the user's actual request.

## What you must do when invoked

**Path note, read before the first command below.** Every
`scripts/erp_client.py` invocation in this document is relative to this
skill's own directory — `skills/qkeee-erp/qkeee-erp-frappe-core/`
under the active Hermes profile root (full path e.g.
`~/.hermes/profiles/<profile>/skills/qkeee-erp/qkeee-erp-frappe-core/scripts/erp_client.py`).
`cd` into that directory first, or prefix every command with the full
path from your shell's actual working directory. Do not guess a shorter
path — a bare `scripts/erp_client.py`, or
`.../profiles/<profile>/scripts/erp_client.py` with the
`skills/qkeee-erp/qkeee-erp-frappe-core/` segment dropped, both
fail with `No such file or directory` (confirmed live, more than once).
If unsure of the exact path, list the skill's own directory first rather
than guessing a second time.

1. **State the active environment before any read or write.** At the start
   of the session, report which tag + base URL this skill is connected to
   (e.g. "Connected to `qa` (`https://org-qa.erpnext.com`)"). Re-surface
   this reminder when picking work back up after a gap, or before a batch
   of write actions — never go silent about which environment is live.
2. **Register this persona — unconditional, once per session,
   best-effort.** Right after stating the active environment, fire-and-
   forget: `python scripts/erp_client.py --tag <tag> register-persona
   --persona-code qkeee-erp-frappe-core --persona-label "Frappe Core" --
   default-mode read-only`. This upserts the `Qkeee Bot Persona` master
   row — it's not a log and isn't gated on the active tag's
   `QKEEE_ERP_<TAG>_DEBUG`. Check the returned `status` —
   `"failed"` means the row was NOT created (almost
   always because `qkeee-erp-bot-init` hasn't been run on this instance
   yet), even though the command still exits cleanly. Treat `"failed"`
   the same as a `logged_in_as` that looks like a personal account —
   mention it once, proactively, and suggest running `qkeee-erp-bot-init`;
   never silently ignore it, and never let it block the user's actual
   request.
3. **Session id — thread one string through the whole conversation.**
   Pick any stable string (e.g. a locally-generated `local-<timestamp>`,
   or a real conversation/thread id from the surrounding harness) at the
   start of the session and pass it as `--session-id` on every
   subsequent `query`/`get`/`report`/`roles`/`mutate` call — it's a
   plain string correlator on Audit Log rows, not a reference to any
   doctype.
4. **Health check on first real use.** Before the first query/mutate of a
   session, run a connectivity check (`python scripts/erp_client.py --tag
   <tag> health`) and surface a clear error if the URL/credentials are
   wrong, rather than letting a raw HTTP error leak through. `--tag` is
   required by the CLI for health/query/mutate, and `--mode` is required
   for mutate — neither falls back to an ambient shell variable, so a
   stray `QKEEE_ERP_MODE` left over in someone's shell profile can never
   silently override what `qkeee_erp.mode` actually says.
5. **Check whether this actually belongs to a named persona skill
   first.** See the routing table in `references/domain-knowledge.md`.
   If the user's request clearly maps to Employee/Leave/Invoice/PO/
   Customer/Item/Asset/User-and-Role/GL-reporting territory, say so and
   point them to the relevant `qkeee-erp-*` skill rather than duplicating
   work a more expert-tuned skill already does better. Skip straight to
   step 9 (route through `erp_client.py`) for a purely infrastructural
   ask (configure an environment, run a raw query with no ambiguity about
   ownership) that doesn't need investigation.
6. **Resolve the doctype's live metadata before saying anything about
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
7. **Discover installed apps + versions.** Try `python scripts/discover.py
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
8. **Check the knowledge base before researching from scratch.**
   `references/knowledge-base/<env-tag>/<app-name>.md` — read it first if
   it exists. See `references/knowledge-base/README.md` for the file
   convention and template. **If the app is new to the knowledge base,
   research it and write the KB entry** before proposing anything
   substantive — fetch the app's GitHub README/docs (most Frappe-ecosystem
   apps live under the `frappe` GitHub org) for what it's for, its key
   doctypes, and typical workflows; cross-check against live metadata
   from step 6; note any discrepancies rather than silently picking one
   source. For a genuinely org-specific custom app with no public repo,
   the KB entry is built from live metadata + whatever the user explains,
   and that's fine — say so explicitly rather than inventing an upstream
   source.
9. **Route every ERPNext call through `scripts/erp_client.py`.** Don't
   hand-roll HTTP calls elsewhere in this skill's logic — the script is
   the single place auth, env resolution, and the read-only gate are
   enforced. `scripts/discover.py` for metadata/app discovery.
10. **Follow the module plan's six-stage workflow pattern** for anything
    that touches ERPNext data: Intake → Validate → Stage/Draft → Confirm →
    Execute → Report back (see `references/connector-reference.md` for the
    save-draft-then-review-then-submit discipline this implies for any
    create/update).
11. **Advisory-first, always — enforced in code, not just prompt.** Every
    write-capable capability this skill performs itself stages a draft and shows the user the
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
    read-write mode (in their own connector copies) because their capability tables were hand-reviewed at
    design time; nothing this skill investigates has had that review, since the doctype
    wasn't known in advance. If a specific fallback-investigation
    capability ends up trusted and repeated, that's a signal it deserves to graduate into a
    proper persona skill, not a reason to loosen this skill's own default.
12. **Prefer a harness-native HTTP-capable tool if one is discoverable.**
    If the host harness exposes a way to enumerate installed tools/skills
    and one already does authenticated HTTP well, prefer it over shelling
    out to this script. If discovery isn't supported in this harness,
    degrade gracefully to `erp_client.py` — never hard-fail over that.
13. **Only the active-environment tag name (not URL/credentials) may be
    remembered across sessions**, so a reminder like "last used: `qa`" can
    be given at the start of a new session. Credentials and URLs never go
    into agent-curated memory — they live only in environment variables.
14. **Resolve config from `qkeee-erp.env`, never from memory and never by
    reading the file's contents into your own context.** `erp_client.py`
    reads `QKEEE_ERP_<TAG>_BASE_URL`/`_API_KEY`/`_API_SECRET` from a
    dedicated `qkeee-erp.env` file (`$HERMES_HOME/qkeee-erp.env` —
    `_qkeee_env_file_path()` resolves this itself; you never need to compute
    the path), falling back to `os.environ` only if that file is absent.
    This file is deliberately **not** the profile's main `.env` and is
    **not** wired through `required_environment_variables`/Hermes'
    `env_passthrough` allowlist: `execute_code`/`terminal` strip *all* env
    vars from the sandbox by default, and only names a skill statically
    declares in frontmatter survive — since the tag is user-chosen at
    runtime, that list can only ever cover the `DEFAULT` tag declared at
    install time. Any other tag's vars would silently never reach the
    sandbox even if correctly set in the main `.env` — this is exactly what
    happened in a live incident (session `20260819_055121_c0056217`): a
    `DEMO_ERP`-tagged run hit `Missing environment variable(s)`, and the
    only way it recovered was by reading the `.env` file's raw contents
    into the model's own context to reconstruct the values — leaking the
    API key/secret through the LLM prompt. `qkeee-erp.env` fixes this
    structurally: the script reads it directly, bypassing the sandbox
    stripping and the passthrough allowlist entirely, so you never need
    the values yourself.
    **Adding a second/third environment is a runtime action, not a
    reinstall — and must never be done by composing a command that embeds
    the raw secret text, or by reading/catting the file to confirm its
    contents.** Tell the user to append three lines
    (`QKEEE_ERP_<NEWTAG>_BASE_URL`/`_API_KEY`/`_API_SECRET`) to
    `$HERMES_HOME/qkeee-erp.env` themselves — out-of-band, via a file
    editor/SSH session outside this conversation, the same way
    `qkeee-erp-bot-init` already tells them to handle newly-generated keys
    (see that skill's SKILL.md) — then offer to switch
    `qkeee_erp.active_env` to it. `<TAG>` is always the sanitized,
    uppercased form of the active tag. One `qkeee-erp.env` holds every
    tag's vars at once. If any of the three required vars for the active
    tag are missing, `erp_client.py`'s own error names exactly which one —
    relay that, never a generic "auth failed." Two more vars are OPTIONAL
    per tag, same file: `QKEEE_ERP_<TAG>_DEBUG` (defaults false) and
    `QKEEE_ERP_<TAG>_REQUESTED_BY` (no default — a write on a tag without
    this set needs `--requested-by` passed explicitly or the user asked).
    Full rationale: `references/connector-reference.md`'s "Environment /
    tag model" section. Template: `../qkeee-erp.env.example` (sibling to
    the persona skill directories) — point the user at it for the exact
    var names/format, never write the values yourself.
15. **Any interim/scratch file goes under `terminal.cwd`, never `/tmp`.**
    A JSON payload assembled for `render_report.py`/`render_*_draft.py`, a
    staged attachment, or any other file that isn't the final deliverable
    is written under `terminal.cwd` from `config.yaml` — never `/tmp` or
    another ad hoc path, which isn't guaranteed to be the same filesystem,
    isn't scoped to this profile, and isn't guaranteed to persist for the
    session. See `references/connector-reference.md`'s "Interim / scratch
    files" section. Clean scratch files up once the task no longer needs
    them.
16. **Save as draft, review, then submit — never create-and-submit in one
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
| Coverage check / routing | Consult the routing table in `references/domain-knowledge.md` | Defer to a named persona skill when the request clearly fits one |
| Generic resource query | `erp_client.py query <DocType> --filters ... --fields ... [--debug]` | Read-only, always allowed. Response includes `has_more` — if true, narrow filters or raise `--limit` rather than assuming the result set is complete. **Does not return child-table (Table-field) data** — Frappe's list endpoint silently drops it even when named in `--fields`, confirmed live against `<erp-instance>`. Prefer this over `get` whenever child-table data isn't needed — ~25x cheaper (336 bytes vs 8,378 bytes measured on a Sales Order status read). `--debug` forces this read to log to Qkeee Bot Audit Log for this call — normally sourced from `QKEEE_ERP_<TAG>_DEBUG` |
| Single-resource full-doc fetch | `erp_client.py get <DocType> <name> [--debug]` | The only way to get child-table rows (needed for Link-field validity review before a submit). Frappe's single-resource GET ignores `--fields` and always returns everything, so this noise-strips audit metadata + presentation-only HTML fields by default (~38% smaller, confirmed live; `--no-strip` for the raw doc) — never strips Link fields or child tables. `--debug` same as query above |
| Built-in report run | `erp_client.py report "<report_name>" --filters '{...}' [--debug]` | Runs an ERPNext server-side Query/Script Report (`frappe.desk.query_report.run`) instead of hand-aggregating rows — see `references/connector-reference.md` |
| User role lookup | `erp_client.py roles [--user <id>]` | Heuristic authority-check signal when no ERPNext Workflow is configured — see `references/connector-reference.md` |
| Installed-app discovery | `discover.py modules` (primary, plain REST); `discover.py apps` (opportunistic, for version numbers) | `apps`'s RPC method is confirmed blocked on at least one real instance (`PermissionError: not whitelisted`) — try `modules` first, `apps` as a bonus, ask the user to paste Help → About only if exact versions matter and both fail |
| DocType → module → app resolution | `discover.py resolve <DocType>` | The core "which app owns this" lookup — run before assuming any doctype is uncovered custom territory. `app: null` with a non-null `app_lookup_error` means the lookup failed, not that there's confirmed no owning app — don't conflate the two |
| Live field-schema fetch | `discover.py meta <DocType>` | Fieldname/label/fieldtype/reqd/options as they exist on this instance right now — authoritative over any doc. Requires System Manager read access to DocType; a 403 here is a bot-permissions gap to flag, not a sign the doctype is missing |
| Knowledge-base lookup | Read `references/knowledge-base/<env-tag>/<app>.md` | Check before re-researching an app already investigated in a prior session |
| Knowledge-base research + write-up | GitHub README/docs fetch + `references/knowledge-base/<env-tag>/<app>.md` write, per the template in the KB README | Cross-check against live metadata; flag discrepancies, don't silently prefer one source |
| Advisory draft rendering | `render_draft.py <input.json>` | Produces the draft + `confirmation_token`/`issued_at` — always the step before any write this skill performs itself |
| Generic resource mutate | `erp_client.py mutate <DocType> <create\|update\|submit\|cancel\|delete> --confirmation-token ... --issued-at ...` (`gated_mutate_resource()`) | Gated by `qkeee_erp.mode` + a resolved requester (inherited from `mutate_resource()`), **plus** a `confirmation_token`/`issued_at` from `render_draft.py` required in code — unconditionally, regardless of mode. Posts a best-effort audit Comment naming the requester on success, and always logs a two-phase Attempted→Success/Failure row to Qkeee Bot Audit Log (best-effort, never blocks the write — see Audit trail section above). No un-gated mutate path exists in this skill's own CLI — persona skills' own copies still expose plain `mutate_resource()` for their design-time-reviewed capabilities |
| Connectivity health check | `erp_client.py health` | Run before first read/write of a session |
| Harness capability discovery | Ask the harness (if it exposes tool listing) whether a native HTTP/report tool already exists | Applies to this skill and is the general pattern other qkeee-erp-* skills should follow too |

## Files

- `scripts/erp_client.py` — the connector implementation (stdlib-only
  Python; no third-party deps so persona skills can copy it verbatim).
  Includes the Qkeee Bot audit-trail retrofit:
  `record_audit_log_start()`/`record_audit_log_finish()` (two-phase write
  logging, wired into `mutate_resource()`), debug-gated read logging in
  `query_resource()`/`get_resource()`, and this skill's own
  `gated_mutate_resource()` write entry point (not synced to persona
  copies — see `scripts/sync_to_personas.py`).
- `scripts/discover.py` — installed-app discovery, DocType → module → app
  resolution, live field-schema fetch. Every persona skill's synced copy
  gets this too, whether or not that persona currently uses it (matches
  how `erp_client.py` itself is synced everywhere).
- `scripts/confirm_token.py` — shared confirmation-token primitives
  (`compute_token`/`is_fresh`) plus this skill's own
  `advisory_write_token()` constructor, used by `render_draft.py`/
  `gated_mutate_resource()`. Persona skills that need a double-confirm
  gate (e.g. `qkeee-erp-fixed-asset-manager`, `qkeee-erp-system-admin`)
  build their own capability-specific constructors on top of the two
  shared primitives in their own copy of this file.
- `scripts/render_draft.py` — this skill's own addition: formats the
  advisory-first draft and computes the `confirmation_token`/`issued_at`
  pair `gated_mutate_resource()` requires. Not part of the canonical
  connector persona skills copy (their capability tables were reviewed
  at design time — they don't need this).
- `references/connector-reference.md` — endpoint table, auth details,
  env/tag model, the advisory-first write gate, and the source-of-truth
  doc for anyone syncing a persona skill's connector copy from this
  canonical one.
- `references/domain-knowledge.md` — the routing table, the SME
  investigation method, and the advisory-first extra-caution rule for
  this skill's fallback-investigation identity.
- `references/knowledge-base/` — per-environment-tag, per-app research
  notes this skill accumulates over sessions. See its `README.md` for the
  file convention. Non-secret only (no URLs/credentials — those stay in
  env vars per the module plan).
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
`qkeee-erp-*` skill (`scripts/sync_to_personas.py` pushes it out, merge-
not-overwrite). Defers to the eight named persona skills whenever a
request fits their coverage (see routing table) — never duplicates work a
more expert-tuned skill already does better. If a fallback-investigation
capability becomes trusted and repeatedly used, consider promoting it
into a new or existing persona skill rather than growing this skill's own
scope indefinitely.
