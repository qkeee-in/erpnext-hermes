---
name: qkeee-erp-bot-init
description: "Provisions Qkeee Bot audit-trail doctypes and bot user."
metadata:
  hermes:
    tags: [ERPNext, Infrastructure, Provisioning, Audit-Trail, Bot-Setup]
    related_skills: [qkeee-erp-frappe-core]
    config:
      - key: qkeee_erp.active_env
        prompt: "Which environment tag should this init run against?"
        default: "default"
    required_environment_variables:
      - name: "QKEEE_ERP_DEFAULT_BASE_URL"
        prompt: "ERPNext site URL for this environment (e.g. https://org.erpnext.com)"
      - name: "QKEEE_ERP_DEFAULT_API_KEY"
        prompt: "ELEVATED (System Manager/Administrator) API key — NOT the steady-state qkeee-erp-bot service account. Creating DocType/Role records needs permission that account should not hold day-to-day."
      - name: "QKEEE_ERP_DEFAULT_API_SECRET"
        prompt: "API secret matching the elevated key above"
---

# qkeee-erp-bot-init

Technical/infrastructure skill, run occasionally (setup, or after a schema
change), not a persona a user talks to for functional work. `init_bot.py`
provisions, in ONE combined dry-run/confirm-token round trip: the `Qkeee
Bot` role, the 2 `Qkeee Bot *` audit-trail doctypes, every persona in
`scripts/doctype_defs.py`'s `PERSONA_MANIFEST` (the `Qkeee Bot Persona`
master row for each shipped `qkeee-erp-*` skill, audited on creation —
see bot-doctypes-design.md), the `qkeee-erp.env` credentials file skeleton
if it doesn't exist yet (header only, no secrets — see "Bot user
provisioning" below for why), and — if `--bot-email` is given — the
dedicated bot-user account itself (create-or-update, role, enable, API
keys). Idempotent — safe to re-run against an instance that's already
initialized, each piece existence-checked independently. Live-validated
end-to-end (dry-run → real-run → idempotent re-run) against
`demo.qkeee.in` — see `references/bot-doctypes-design.md`'s
deferred-field-patch note for what that run found and fixed.

**`--bot-email` folds bot-user provisioning into the same run** — the
dedicated `qkeee-erp-bot@<org>`-style service-account User the persona
skills' shared `QKEEE_ERP_<TAG>_API_KEY`/`_API_SECRET` should authenticate
as. Omit it to skip bot-user provisioning entirely and run
`scripts/ensure_bot_user.py` separately instead (its own standalone
dry-run/confirm flow, unchanged — see "Bot user provisioning" below).

## When to Use

Use when setting up a fresh ERPNext instance for the `qkeee-erp-*`
skill library, when a persona skill reports the audit doctypes are
missing or that its credentials don't look like a dedicated bot
account, or when explicitly asked to "initialize the bot" / "set up
the audit trail" / "create a bot user" / "run bot init" against an
environment.

## Bot user provisioning

**Two ways to run this, pick one per invocation:**
- **Folded into `init_bot.py`** via `--bot-email` (see Procedure below) —
  one dry-run/confirm-token round trip covers role + doctypes + personas
  + env-file skeleton + bot user together. Preferred for a fresh
  instance/first-time setup.
- **Standalone** via `scripts/ensure_bot_user.py` — its own separate
  dry-run/confirm-token flow, for provisioning/rotating the bot user
  independently of a schema init (e.g. `--regenerate-keys` on an
  already-initialized instance, or adding the bot user well after the
  doctypes were set up). Requires the `Qkeee Bot` role to already exist —
  errors out with that instruction if it doesn't (run the doctype/role
  init flow first).

Either way, it checks whether the given ERPNext email already exists as a
User, and if so whether it holds the `Qkeee Bot` role, is enabled, and has
an API key configured — using the elevated admin credential, creates it
if missing (System User, no welcome email, `Qkeee Bot` role attached) and
generates a fresh API key/secret pair.

**Trigger this proactively, not only when explicitly asked to "init the
bot."** If a user is setting up ERPNext credentials for the first time, or
a persona skill's health check reports `logged_in_as` an identity that
looks like a real person rather than a service account, or the user
mentions they don't yet have a dedicated bot account: surface this
capability and offer to run it, rather than waiting to be asked by name.

1. **Ask for (or confirm) the bot email** the user wants — suggest a
   `qkeee-erp-bot@<org-domain>`-style address if they don't have one in
   mind, but let them choose; don't invent one silently.
2. **Dry-run first**: either `python scripts/init_bot.py --tag <tag>
   --requested-by <admin-id> --bot-email <email> --dry-run` (folded-in
   path) or `python scripts/ensure_bot_user.py --tag <tag> --bot-email
   <email> --requested-by <admin-id> --dry-run` (standalone path). Shows
   exactly what would change (create user / assign role / re-enable /
   generate keys, plus role/doctypes/personas for the folded-in path) and
   prints a `--confirm-token`/`--issued-at` pair.
3. **Run for real only after the user explicitly confirms** — including
   confirming that this email really is meant to be a dedicated bot
   account, not someone's personal login (the whole point of the
   Bot-account non-negotiable in `qkeee-erp-frappe-core`'s SKILL.md). Pass the
   token back verbatim, to whichever script printed it.
4. **If new keys are generated, they print to stdout exactly once.** Tell
   the user to copy `QKEEE_ERP_<TAG>_API_KEY` / `_API_SECRET` into
   **`$HERMES_HOME/qkeee-erp.env`** (the dedicated, isolated file
   `erp_client.py`'s `_qkeee_env_file_path()` reads — deliberately separate
   from the profile's main `.env`/`env_passthrough` mechanism; see
   `qkeee-erp-frappe-core/SKILL.md`'s "Resolve config" section for why) or
   OS credential manager immediately. **Both paths create the file itself
   if it's missing (`ensure_qkeee_env_file_skeleton()`) — header comment
   only, no `BASE_URL`/`API_KEY`/`API_SECRET` lines — so the user has
   somewhere to paste into, never a file this tooling pre-fills with the
   actual secret.** This skill never stores real credential values
   anywhere itself (not in the Qkeee Bot audit trail, not in a file, not
   in agent memory). **The copy-the-real-values step happens out-of-band,
   on the user's own machine — never by you reading the keys back out of
   stdout and re-emitting them into a command, and never by catting/
   reading `qkeee-erp.env` afterward to confirm it landed correctly**; if
   the user wants confirmation, have them check the file themselves. If
   lost, re-run `ensure_bot_user.py --regenerate-keys` (through its own
   dry-run/confirm flow) to issue a new pair — this invalidates the old
   one.
6. **If the user says they'd rather create/share the bot user themselves**
   (e.g. their org's ERPNext admin access is restricted to specific
   people), don't push — tell them what's needed: a dedicated User with
   the `Qkeee Bot` role, enabled, with an API key/secret generated via
   *User → API Access → Generate Keys* in the ERPNext UI, then have them
   set the three `QKEEE_ERP_<TAG>_*` env vars themselves.

## Pitfalls

**Never run this against a target using the shared `qkeee-erp-bot@<org>`
steady-state service account.** Creating `DocType`/`Role` records requires
System Manager–level permission; giving the day-to-day bot account that
level of access would violate least-privilege and widen its blast radius
far beyond what the persona skills need. This skill's credentials must be
an elevated/admin key, used for init only, distinct from the persona
skills' shared bot account (which continues to use its own, narrower key
against the same tag). This is enforced by ERPNext's own server-side
permission model — a non-System-Manager key gets a 403 on the DocType/Role
create calls themselves — not by a pre-check in this skill's own code.

**This skill is exempt from `qkeee_erp.mode`.** Unlike every persona
skill, it doesn't declare `qkeee_erp.mode` in config and always operates
in write mode (`init_bot.py` passes `mode="read-write"` unconditionally)
— schema provisioning isn't the kind of business write that toggle was
built to gate. The actual controls here are the elevated-credential
requirement above and the confirm-token flow below.

## Procedure

**Path note, read before the first command below.** Every
`scripts/erp_client.py` invocation in this document is relative to this
skill's own directory — `skills/qkeee-erp/qkeee-erp-bot-init/`
under the active Hermes profile root (full path e.g.
`~/.hermes/profiles/<profile>/skills/qkeee-erp/qkeee-erp-bot-init/scripts/erp_client.py`).
`cd` into that directory first, or prefix every command with the full
path from your shell's actual working directory. Do not guess a shorter
path — a bare `scripts/erp_client.py`, or
`.../profiles/<profile>/scripts/erp_client.py` with the
`skills/qkeee-erp/qkeee-erp-bot-init/` segment dropped, both
fail with `No such file or directory` (confirmed live, more than once).
If unsure of the exact path, list the skill's own directory first rather
than guessing a second time.

1. **Confirm which environment tag this init targets, out loud, before
   doing anything.** Getting this wrong means creating doctypes in the
   wrong instance (e.g. prod instead of qa).
2. **Confirm the configured API key for this tag is an elevated/admin
   credential, not the persona skills' shared bot key**, before running.
   If the user isn't sure, tell them to check which ERPNext user the key
   belongs to (`erp_client.py --tag <tag> health` reports `logged_in_as`)
   and confirm that user holds System Manager in the ERPNext UI — the
   health check itself only reports identity, not roles; the write calls
   below are what actually enforce the permission (they 403 otherwise).
3. **Run a dry-run first**: `python scripts/init_bot.py --tag <tag>
   --requested-by <admin-id> --dry-run` — add `--bot-email <email>` too if
   the bot-user account should be provisioned in this same pass (see "Bot
   user provisioning" above). This prints the plan (exactly what would be
   created — role, doctypes, personas, `qkeee-erp.env` skeleton if
   missing, and the bot user if `--bot-email` was given) and a
   `--confirm-token`/`--issued-at` pair covering all of it as one unit.
   Show the plan to the user before doing anything for real.
4. **Run for real only after the user explicitly confirms the printed
   plan**, passing the token back verbatim (and the SAME `--bot-email`
   value if it was on the dry-run — a mismatched or dropped `--bot-email`
   changes the plan and the token won't match):
   `python scripts/init_bot.py --tag <tag> --requested-by <admin-id>
   [--bot-email <email>] --confirm-token <token> --issued-at <issued_at>`.
   This is code-enforced, not just prompt-instructed (see
   `scripts/confirm_token.py`'s `full_init_plan_token()`): `init_bot.py`
   recomputes the token from the target's *current* live state and
   refuses to proceed if it doesn't match (target changed since the
   dry-run, wrong tag/requester/bot-email, or a tampered/copied-from-
   elsewhere token) or if more than 15 minutes have passed since the
   dry-run. Never compute a token and immediately consume it in the same
   turn — only pass `--confirm-token` after the user's own reply
   affirmatively confirms the printed plan. Idempotent — records that
   already exist are skipped, not recreated or overwritten; if nothing
   needs creating/changing, no token is required. Report the summary
   (`doctypes_created`/`doctypes_already_present`, `personas`,
   `qkeee_env_file_created`, `bot_user`) back to the user — if
   `--bot-email` generated fresh keys, they print to stdout once (see
   step 4 of "Bot user provisioning" above for what to tell the user to
   do with them).
5. **This skill provisions schema only — it does not itself write audit
   rows.** Actually calling into these doctypes on every read/write (the
   two-phase `Attempted`→`Success`/`Failure` Audit Log write, the
   `AUDIT_EXEMPT_DOCTYPES` recursion guard) lives in `qkeee-erp-frappe-
   core`'s connector and every persona skill's synced copy of it — that
   retrofit has already landed and is synced across all 7 write-capable
   persona skills (verified: `record_audit_log_start` present in every
   copy). Once this skill's schema exists on a target, persona writes
   against that target start logging immediately — no separate step
   needed. If a user runs this skill against a target for the first
   time, audit rows should appear as soon as the first persona write
   happens afterward.
6. **Ground every doctype/field/permission decision in
   `references/bot-doctypes-design.md`** — that file, not this one, is
   the source of truth for the schema. If a user asks to add a field or
   change a permission, update the design doc first, then
   `scripts/doctype_defs.py` to match, then re-run init. Reconciling
   arbitrary field-level drift on an already-created doctype is a known
   gap, not yet built.
7. **Prefer a harness-native HTTP-capable tool if discoverable**, same
   discovery-first pattern as every other `qkeee-erp-*` skill.

## Quick Reference

| Capability | How | Notes |
| --- | --- | --- |
| Doctype/role existence check | `erp_client.py resource_exists()` (404-tolerant GET) | Read-only, always safe to run |
| Role provisioning | `init_bot.py` creates `Role: Qkeee Bot` if missing | Desk-access role, no doctype permissions of its own beyond what each doctype's `permissions` array grants it |
| Doctype provisioning | `init_bot.py` creates each of the 2 `Qkeee Bot *` doctypes if missing, via `mutate_resource("DocType", "create", ...)` | `custom: 1`, module `Custom` — no app, no Python controller. See design doc for why |
| Persona provisioning | `init_bot.py` calls `ensure_persona_registered()` for every entry in `doctype_defs.PERSONA_MANIFEST` | Idempotent per persona; audited on creation (see bot-doctypes-design.md — `Qkeee Bot Persona` is no longer `AUDIT_EXEMPT_DOCTYPES`) |
| `qkeee-erp.env` skeleton | `init_bot.py` calls `erp_client.ensure_qkeee_env_file_skeleton()` if the file doesn't exist | Header comment only, no secrets — see "Bot user provisioning" above for why this tooling never writes the real values itself |
| Dry-run | `init_bot.py --dry-run [--bot-email <email>]` | Reports what would be created/changed (role, doctypes, personas, env-file skeleton, and bot user if `--bot-email` given) and issues one `--confirm-token`/`--issued-at` pair covering all of it, without writing anything |
| Connectivity health check | `erp_client.py health` | Run before init; also confirms which ERPNext user the configured key belongs to |
| Bot user existence/role/enabled/key check | `ensure_bot_user.py --dry-run` (standalone) or folded into `init_bot.py --bot-email --dry-run` | Read-only-equivalent — reports what's missing without writing anything; the standalone path requires the `Qkeee Bot` role to already exist, the folded-in path doesn't (role may be created in the same run) |
| Bot user provisioning | `ensure_bot_user.py` (standalone) or `init_bot.py --bot-email` (folded-in) creates the User (if missing), assigns the `Qkeee Bot` role, re-enables if disabled, and generates a fresh API key/secret if none is configured | Same dry-run → confirm-token → real-run discipline either way; keys print once, never stored by this skill; `--regenerate-keys` is standalone-only |

## Verification

Never run for real without a prior dry-run's `--confirm-token`/
`--issued-at` — both flows recompute the token from the target's
current live state and refuse to proceed on a mismatch or after 15
minutes. After a real run, confirm the reported `doctypes_created` vs
`doctypes_already_present` (or the bot-user equivalent) matches what
the dry-run's plan said would happen.

## Files

- `references/bot-doctypes-design.md` — the canonical, buildable spec for
  both doctypes: full field tables, permission matrix, the two-phase
  Attempted/Success/Failure write discipline, the debug-mode volume gate,
  the `AUDIT_EXEMPT_DOCTYPES` recursion guard, and the decision log this
  design came from. Read this before extending or modifying the schema.
- `scripts/doctype_defs.py` — the actual `DocType`/`Role` create payloads,
  synced from the design doc, plus `PERSONA_MANIFEST` (persona_code/label
  for every shipped `qkeee-erp-*` persona — hand-maintained, add an entry
  here when a new persona skill ships).
- `scripts/erp_client.py` — connector copy (self-contained-copies pattern,
  synced from `qkeee-erp-frappe-core` including its two-phase audit-log write
  path), plus `resource_exists()` — a 404-tolerant existence check this
  skill adds on top of the shared connector shape. Credentials for this
  copy must be elevated (see non-negotiable above). Diverges from core
  only in `SKILL_LABEL` and in restricting `mutate` to create/update — see
  the file's own module docstring for the sync/divergence contract.
- `scripts/confirm_token.py` — the dry-run → real-run confirm-token
  backstop (same pattern as `qkeee-erp-system-admin`'s file of the same
  name): tokens the exact create-plan so a real run can't proceed on a
  stale or tampered token, or without a prior dry-run at all.
- `scripts/init_bot.py` — the combined init flow: health check → compute
  plan (role, doctypes, personas, and — if `--bot-email` given — the bot
  user, all against current live state) → (dry-run: print plan + one
  combined token) or (real: verify token → ensure role → ensure each
  doctype → register every manifest persona → ensure the `qkeee-erp.env`
  skeleton exists → provision the bot user if `--bot-email` given),
  existence-checked and idempotent throughout. `ensure_bot_user_step()`
  inlines the same create/update/keygen logic `ensure_bot_user.py` uses
  standalone, so the folded-in path reuses `init_bot.py`'s own
  already-verified combined token instead of `ensure_bot_user.py`'s
  narrower one.
- `scripts/ensure_bot_user.py` — the standalone bot-user flow, for
  provisioning/rotating the bot user independently of a schema init:
  health check → compute plan (user exists? has `Qkeee Bot` role? enabled?
  has an API key?) → (dry-run: print plan + token) or (real: verify token
  → create-or-update the User → generate API keys if needed, printed once).
  Requires the `Qkeee Bot` role to already exist (run `init_bot.py` first)
  — `compute_plan()`'s `require_role_exists` param exists only so
  `init_bot.py`'s combined dry-run can reuse this same plan logic before
  the role has been created yet; standalone invocation always uses the
  default (hard-fail) behavior.
- `scripts/test_erp_client.py`, `scripts/test_init_bot.py`,
  `scripts/test_doctype_defs.py`, `scripts/test_ensure_bot_user.py` — unit
  coverage for the connector gating, the plan/token flows, the doctype
  payload shapes, and the bot-user provisioning flow.

## Extension point

To target a different ERP backend, this entire skill's premise (Frappe
`DocType`/`Role` records, `custom: 1`, `Custom` module) would need to be
rebuilt against that backend's own schema-provisioning mechanism — unlike
the persona skills' domain-knowledge layers, this skill is Frappe-specific
by design, not ERP-agnostic.

## Relationships

Provisions the schema `qkeee-erp-frappe-core`'s connector (and every
persona skill's synced copy of it) writes audit rows to — see Procedure
step 5. Run this once per target environment tag before any persona
skill's writes against that tag, so the first write doesn't hit missing
doctypes.
