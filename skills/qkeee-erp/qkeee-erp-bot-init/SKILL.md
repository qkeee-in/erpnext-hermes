---
name: qkeee-erp-bot-init
description: "Provisions the Qkeee Bot audit-trail doctypes (Qkeee Bot Persona, Qkeee Bot Session, Qkeee Bot Message, Qkeee Bot Audit Log) plus the Qkeee Bot role into a target ERPNext instance, if they don't already exist — idempotent, no custom app required. Also detects/provisions the dedicated qkeee-erp-bot service-account User the persona skills' shared API key should belong to, assigning it the Qkeee Bot role and generating fresh API keys if needed. Use when setting up a fresh ERPNext instance for the qkeee-erp-* skill library, when a persona skill reports the audit doctypes are missing or that its credentials don't look like a dedicated bot account, or when explicitly asked to 'initialize the bot' / 'set up the audit trail' / 'create a bot user' / 'run bot init' against an environment."
metadata:
  hermes:
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
change), not a persona a user talks to for functional work. Checks whether
the 4 `Qkeee Bot *` audit-trail doctypes and the `Qkeee Bot` role already
exist in the target ERPNext instance, and creates whatever's missing.
Idempotent — safe to re-run against an instance that's already initialized.
Live-validated end-to-end (dry-run → real-run → idempotent re-run) against
`demo.qkeee.in` — see `references/bot-doctypes-design.md`'s
deferred-field-patch note for what that run found and fixed.

**Also checks for and can provision the dedicated
`qkeee-erp-bot@<org>`-style service-account User** the persona skills'
shared `QKEEE_ERP_<TAG>_API_KEY`/`_API_SECRET` should authenticate as —
see "Bot user provisioning" below. This is a related but separate concern
from the doctype/role provisioning above: the doctypes/role are the audit
*schema*; the bot user is the *account* persona skills actually log in as
day to day. Both are this skill's job because both are one-time/occasional
per-environment setup an elevated admin credential is needed for.

## Bot user provisioning

Run `scripts/ensure_bot_user.py` to check whether a given ERPNext email
already exists as a User, and if so whether it holds the `Qkeee Bot` role,
is enabled, and has an API key configured. If the User doesn't exist yet,
this script — using the same elevated admin credential as the doctype
provisioning above — can create it (System User, no welcome email, `Qkeee
Bot` role attached) and generate a fresh API key/secret pair for it.

**Trigger this proactively, not only when explicitly asked to "init the
bot."** If a user is setting up ERPNext credentials for the first time, or
a persona skill's health check reports `logged_in_as` an identity that
looks like a real person rather than a service account, or the user
mentions they don't yet have a dedicated bot account: surface this
capability and offer to run it, rather than waiting to be asked by name.

1. **Requires the `Qkeee Bot` role to already exist.** Run the doctype/role
   init flow (steps above) first if it doesn't — `ensure_bot_user.py`
   errors out with this instruction rather than silently creating a user
   with a role that doesn't exist.
2. **Ask for (or confirm) the bot email** the user wants — suggest a
   `qkeee-erp-bot@<org-domain>`-style address if they don't have one in
   mind, but let them choose; don't invent one silently.
3. **Dry-run first**: `python scripts/ensure_bot_user.py --tag <tag>
   --bot-email <email> --requested-by <admin-id> --dry-run`. Shows exactly
   what would change (create user / assign role / re-enable / generate
   keys) and prints a `--confirm-token`/`--issued-at` pair, same
   discipline as the doctype-provisioning flow above.
4. **Run for real only after the user explicitly confirms** — including
   confirming that this email really is meant to be a dedicated bot
   account, not someone's personal login (the whole point of the
   Bot-account non-negotiable in `qkeee-erp-core`'s SKILL.md). Pass the
   token back verbatim.
5. **If new keys are generated, they print to stdout exactly once.** Tell
   the user to copy `QKEEE_ERP_<TAG>_API_KEY` / `_API_SECRET` into
   **this agent profile's own `.env` file**
   (`.hermes/profile/<profile-name>/.env` — substitute the real profile
   name, never a repo-root or cross-profile `.hermes/.env`) or OS
   credential manager immediately — this skill never stores them anywhere
   (not in the Qkeee Bot audit trail, not in a file, not in agent memory).
   If lost, re-run with `--regenerate-keys` (through the same
   dry-run/confirm flow) to issue a new pair — this invalidates the old
   one.
6. **If the user says they'd rather create/share the bot user themselves**
   (e.g. their org's ERPNext admin access is restricted to specific
   people), don't push — tell them what's needed: a dedicated User with
   the `Qkeee Bot` role, enabled, with an API key/secret generated via
   *User → API Access → Generate Keys* in the ERPNext UI, then have them
   set the three `QKEEE_ERP_<TAG>_*` env vars themselves.

## The non-negotiable

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

## What you must do when invoked

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
   --requested-by <admin-id> --dry-run`. This prints the plan (exactly
   what would be created) and a `--confirm-token`/`--issued-at` pair.
   Show the plan to the user before doing anything for real.
4. **Run for real only after the user explicitly confirms the printed
   plan**, passing the token back verbatim:
   `python scripts/init_bot.py --tag <tag> --requested-by <admin-id>
   --confirm-token <token> --issued-at <issued_at>`.
   This is code-enforced, not just prompt-instructed (see
   `scripts/confirm_token.py`): `init_bot.py` recomputes the token from
   the target's *current* live state and refuses to proceed if it
   doesn't match (target changed since the dry-run, wrong tag/requester,
   or a tampered/copied-from-elsewhere token) or if more than 15 minutes
   have passed since the dry-run. Never compute a token and immediately
   consume it in the same turn — only pass `--confirm-token` after the
   user's own reply affirmatively confirms the printed plan. Idempotent
   — records that already exist are skipped, not recreated or
   overwritten; if nothing needs creating, no token is required. Report
   the summary (`doctypes_created` vs `doctypes_already_present`) back to
   the user.
5. **This skill provisions schema only — it does not wire up runtime
   audit logging.** Actually calling into these doctypes on every
   read/write (the two-phase `Attempted`→`Success`/`Failure` Audit Log
   write, the `in_reply_to` message linkage, the `AUDIT_EXEMPT_DOCTYPES`
   recursion guard) is `qkeee-erp-core` connector work — a follow-up
   retrofit across `erp_client.py` and the persona skills that copy it,
   not something this skill does at init time. Say so if a user expects
   audit rows to start appearing immediately after running this.
6. **Ground every doctype/field/permission decision in
   `references/bot-doctypes-design.md`** — that file, not this one, is
   the source of truth for the schema. If a user asks to add a field or
   change a permission, update the design doc first, then
   `scripts/doctype_defs.py` to match, then re-run init. The one field-
   drift case this skill currently handles is the mutual Message ↔ Audit
   Log reference (`DEFERRED_FIELD_PATCHES` / `ensure_deferred_fields()`,
   run automatically as the last step of every real run) — beyond that
   specific pattern, reconciling arbitrary future field-level drift on an
   already-created doctype is still a known gap, not yet built.
7. **Prefer a harness-native HTTP-capable tool if discoverable**, same
   discovery-first pattern as every other `qkeee-erp-*` skill.

## Capabilities

| Capability | How | Notes |
| --- | --- | --- |
| Doctype/role existence check | `erp_client.py resource_exists()` (404-tolerant GET) | Read-only, always safe to run |
| Role provisioning | `init_bot.py` creates `Role: Qkeee Bot` if missing | Desk-access role, no doctype permissions of its own beyond what each doctype's `permissions` array grants it |
| Doctype provisioning | `init_bot.py` creates each of the 4 `Qkeee Bot *` doctypes if missing, via `mutate_resource("DocType", "create", ...)` | `custom: 1`, module `Custom` — no app, no Python controller. See design doc for why |
| Dry-run | `init_bot.py --dry-run` | Reports what would be created and issues a `--confirm-token`/`--issued-at` pair, without writing anything |
| Connectivity health check | `erp_client.py health` | Run before init; also confirms which ERPNext user the configured key belongs to |
| Bot user existence/role/enabled/key check | `ensure_bot_user.py --dry-run` | Read-only-equivalent — reports what's missing without writing anything; requires the `Qkeee Bot` role to already exist |
| Bot user provisioning | `ensure_bot_user.py` creates the User (if missing), assigns the `Qkeee Bot` role, re-enables if disabled, and generates a fresh API key/secret if none is configured | Same dry-run → confirm-token → real-run discipline as doctype provisioning; keys print once, never stored by this skill |

## Files

- `references/bot-doctypes-design.md` — the canonical, buildable spec for
  all 4 doctypes: full field tables, permission matrix, the two-phase
  Attempted/Success/Failure write discipline, the debug-mode volume gate,
  the `AUDIT_EXEMPT_DOCTYPES` recursion guard, and the decision log this
  design came from. Read this before extending or modifying the schema.
- `scripts/doctype_defs.py` — the actual `DocType`/`Role` create payloads,
  synced from the design doc. Field-for-field source of what gets created.
  Also defines `DEFERRED_FIELD_PATCHES`: fields that can't be in their
  doctype's initial create payload because they Link to a doctype created
  later (live-confirmed that Frappe rejects that at create
  time) — currently just `Qkeee Bot Message.linked_audit_log`.
- `scripts/erp_client.py` — connector copy (self-contained-copies pattern,
  synced from `qkeee-erp-core` including its two-phase audit-log write
  path), plus `resource_exists()` — a 404-tolerant existence check this
  skill adds on top of the shared connector shape. Credentials for this
  copy must be elevated (see non-negotiable above). Diverges from core
  only in `SKILL_LABEL` and in restricting `mutate` to create/update — see
  the file's own module docstring for the sync/divergence contract.
- `scripts/confirm_token.py` — the dry-run → real-run confirm-token
  backstop (same pattern as `qkeee-erp-system-admin`'s file of the same
  name): tokens the exact create-plan so a real run can't proceed on a
  stale or tampered token, or without a prior dry-run at all.
- `scripts/init_bot.py` — the init flow: health check → compute plan →
  (dry-run: print plan + token) or (real: verify token → ensure role →
  ensure each doctype → `ensure_deferred_fields()`), existence-checked
  and idempotent throughout.
- `scripts/ensure_bot_user.py` — the bot-user flow:
  health check → compute plan (user exists? has `Qkeee Bot` role? enabled?
  has an API key?) → (dry-run: print plan + token) or (real: verify token
  → create-or-update the User → generate API keys if needed, printed once).
  Requires the `Qkeee Bot` role to already exist (run `init_bot.py` first).
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

Provisions the schema `qkeee-erp-core`'s connector (and every persona
skill's copy of it) will eventually write to, once the audit-logging
retrofit described in step 5 above is built. Run this once per target
environment tag, before that retrofit lands or as soon as it does.
