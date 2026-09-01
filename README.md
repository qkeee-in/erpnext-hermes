# erpnext-hermes Agent Profile

Git-tracked source of truth for a Hermes Agent profile specialized as an ERPNext functional consultant and operations agent — running the single `qkeee-erp-associate` skill across HR/Payroll, Accounts, Inventory, Procurement, Sales, Fixed Assets, System Admin, MIS reporting, Manufacturing, and Doc Extraction.

**Live Hermes profile:** `~/.hermes/profiles/dev-erpnext`
Hermes reads these portable files through symlinks from the live profile directory back into this repo — edit here, not in `~/.hermes/profiles/dev-erpnext` directly.

## What this profile is

An ERPNext specialist that acts like a functional consultant, not a click-executor — it explains the "why" behind a process, detects per-instance customizations before assuming stock ERPNext behavior, and treats every submittable document as requiring human review before it goes live. Full scope boundaries (owns / should-not-own) are defined in [`profile.md`](./profile.md); identity and voice are defined in [`SOUL.md`](./SOUL.md).

## Directory layout

### Tracked and symlinked (edit these, in this repo)

| File / dir | Purpose |
|---|---|
| `distribution.yaml` | Profile manifest / distribution metadata — `distribution_owned` lists exactly what `hermes profile update` is allowed to overwrite (`SOUL.md`, `skills/qkeee-erp/`, `cron/jobs.json`, `config.yaml`, `mcp.json`) |
| `SOUL.md` | Agent identity, voice, personality — loaded into system prompt slot #1 |
| `config.yaml` | Model, provider, toolsets, `skills.external_dirs`, `skills.write_approval` |
| `mcp.json` | MCP server connections (currently no servers configured; ERPNext access goes through `qkeee-erp-associate`'s REST connector scripts) |
| `skills/qkeee-erp/` | The `qkeee-erp-associate` skill — mounts read-only into the live profile via `skills.external_dirs`, edited here only |
| `cron/` | Scheduled jobs (e.g. recurring MIS reports); currently empty |
| `profile.md` | Purpose / Owns / Should-Not-Own / safety policy / operating protocol for this agent — user-owned, not replaced on `profile update` |

**Skill** (`skills/qkeee-erp/qkeee-erp-associate/`) — one skill, thin `SKILL.md` router, domain procedures loaded on demand:

| Path | Role |
|---|---|
| `scripts/core/client.py` | Shared connector — auth, discovery, RBAC pre-check, write-allowlist gate, PII redaction, audit logging |
| `scripts/domains/*.py` | Per-domain functions + `ALLOWED_WRITE_DOCTYPES`: `hr_payroll`, `accounts`, `mis` (no write path), `sales`, `procurement`, `inventory`, `fixed_assets`, `system_admin` |
| `references/domains/*.md` | Per-domain procedure, one per module above, plus `manufacturing.md` and `doc-extraction.md` |
| `references/00-conventions.md` | Naming rules, GRC baseline, scope guardrail — single copy, referenced by every domain file |
| `scripts/init_bot.py` | Admin-invoked, one-time: provisions the `Qkeee Bot` Role + `Qkeee Bot Audit Log` doctype |

Domain modules import the shared core directly (same-skill imports) — there is only one connector implementation.

### Runtime-only (never commit)

`.env`, `auth.json`, `memories/`, `sessions/`, `state.db*`, `logs/`, `workspace/`, `plans/`, `home/`, `local/`, `*_cache/`, `tmp/` — these belong to the live profile instance and can carry secrets or grow to tens of GB. See [Profiles: Running Multiple Agents](https://hermes-agent.nousresearch.com/docs/user-guide/profiles) for what a profile directory holds and why.

### ERPNext credentials: `qkeee-erp.env`, not `.env`

ERPNext instance credentials (`QKEEE_ERP_*`) live in their own file at `$HERMES_HOME/qkeee-erp.env`, deliberately **outside** the profile's main `.env`. `scripts/core/client.py` reads this file directly, bypassing Hermes' sandbox env-stripping (`execute_code`/`terminal` sandboxes strip env vars by default; only statically-declared `required_environment_variables` for the DEFAULT tag survive) and the `env_passthrough` allowlist. This also keeps ERPNext secrets physically separate from any LLM-provider key in the main `.env`.

- Copy `skills/qkeee-erp/qkeee-erp-associate/qkeee-erp-associate.env.example` to `$HERMES_HOME/qkeee-erp.env` and fill in real values out-of-band — never by having the agent read/cat this file or echo the values back.
- One file holds every environment **tag** (`qkeee_erp.active_env`): `QKEEE_ERP_<TAG>_BASE_URL` / `_API_KEY` / `_API_SECRET` (required), plus optional `_ALLOW_INSECURE`, `_REQUESTED_BY`, `_ENV_CLASS` per tag. Add a new ERPNext instance by appending another tag's trio, never by creating a second file.
- See `qkeee-erp-associate/references/01-connectivity.md`'s "Env resolution" section for the full rationale.
- Read audit logging is unconditional on every `query_resource()`/`get_resource()`/`run_query_report()` call — there is no debug flag to gate it.

### Audit-trail doctypes

`scripts/init_bot.py` provisions 1 `Qkeee Bot *` doctype directly in ERPNext (no custom app) to give every bot action a compliance-grade trail.

| Doctype | Created | Purpose |
|---|---|---|
| `Qkeee Bot Audit Log` | **Always**, every read and write | One row per ERPNext record read/written by the bot — action, reference doc, before/after payload, field diff, `domain_code` (which `references/domains/*.md` procedure made the call), `user_approved` (Approved/Not Confirmed/Not Required — detection, not a write gate), submittable/locked once resolved |

## Example prompts / tasks this profile handles

**HR & Payroll**
- "Check leave balance for HR-EMP-00014 and apply 3 days casual leave starting next Monday."
- "Run attendance regularization for the engineering team for the days the biometric device was down last week."
- "Draft salary slips for all Support-structure employees for July 2026 — don't submit, I'll review first."
- "Walk me through the exit checklist for HR-EMP-00027, separation date end of month."

**Recruitment**
- "Open a Job Opening for a Senior Frappe Developer, and draft an Offer Letter for the candidate we discussed — advisory draft only."

**Accounts & MIS**
- "Pull this month's AR aging report, grouped by customer."
- "Draft a Journal Entry to correct the misclassified expense from PR 4021 — show me before submitting."

**Inventory / Procurement / Sales**
- "What's current stock for item X across all warehouses?"
- "Draft a Purchase Order for the reorder list you flagged last week — I'll confirm before submit."

**Process / customization research**
- "We use a custom doctype for asset warranty tracking — check the live schema and tell me what's mandatory before I create a new record."
- "This ERPNext instance behaves differently from stock on Sales Invoice — is that a known customization or a bug? Check discuss.frappe.io if the docs don't explain it."

**System admin**
- "List all users with System Manager role and flag anyone who hasn't logged in in 90 days."

Every example ending in submission (payslips, journal entries, purchase orders, etc.) stops for review and explicit confirmation before the write — no exceptions. See the **Safety Policy** section of [`profile.md`](./profile.md).

## Hermes CLI: profile from this repo

This repo *is* a Hermes profile distribution (`distribution.yaml`). Standard flow:

**Install fresh** (clones this repo, validates the manifest, copies distribution-owned files, checks required env vars):
```bash
hermes profile install github.com/qkeee-in/erpnext-hermes --alias --name dev-erpnext
```

**Pull latest after a commit lands here** (fetches from the recorded source; preserves your local `config.yaml` edits unless overridden):
```bash
hermes profile update dev-erpnext
hermes profile update dev-erpnext --force-config   # reset config.yaml to distro defaults
```

**Inspect / list:**
```bash
hermes profile info dev-erpnext   # manifest: version, author, required env vars
hermes profile list               # all local profiles, distribution source column
```

`hermes profile update` only ever touches what `distribution.yaml`'s `distribution_owned` lists — currently `SOUL.md`, `skills/qkeee-erp/`, `cron/jobs.json`, `config.yaml`. Runtime state (`.env`, `qkeee-erp.env`, `memories/`, `sessions/`, etc.) is never touched by install/update.

Other profile commands (not specific to this repo, general Hermes usage): `hermes profile create`, `hermes profile show`, `hermes profile rename`, `hermes profile delete`, `hermes profile use <name>` (set default), `hermes profile export` / `hermes profile import` (tar.gz, for one-off sharing without git).

## Safety & governance

- **Skill write approval:** `skills.write_approval: true` in `config.yaml` stages every agent-initiated skill write (create/edit/patch/delete) under `~/.hermes/pending/skills/` for approve/deny review via `/skills pending`, `/skills diff`, `/skills approve`, `/skills reject` — nothing lands unreviewed. See [Security | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/security).
- **Skill source separation:** `qkeee-erp-associate` (this repo, shipped/pinned) mounts via `skills.external_dirs` (read-only) and should be marked externally-owned so Hermes' autonomous background-review pass can't silently patch its audit/RBAC/GRC logic. The satellite `qkeee-erp-learned/<env-tag>` skills it writes via `skill_manage` (per-instance environment notes — versions, custom doctypes, non-ERPNext API notes) live in the profile's normal local/learned skill space and stay open to that same background review, since letting the agent refine its own instance notes is the point. See `qkeee-erp-associate/references/00-conventions.md` and [Skills System | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills).
- **Save-draft-then-review-then-submit, always:** every docstatus-bearing document requires a review-before-submit step with explicit human confirmation, defined in `profile.md` — and code-enforced for submit/cancel via a fresh confirmation token, not prompt discipline alone (see `references/00-conventions.md`'s Non-negotiable 5).
- **No auth fallbacks:** token auth (`QKEEE_ERP_*` env vars) only — no session-cookie/password workarounds that drop audit attribution.
- **RBAC pre-check + read audit logging, every tag:** `scripts/core/client.py`'s requester-permission check and audit logging both run unconditionally on every environment and every read/write — no PROD-only or debug-only carve-out.
- **Audit log & tracing:** every ERPNext access goes through `scripts/core/client.py`, which stamps audit-log entries with the acting bot's session id, `_REQUESTED_BY`, and the calling domain — no audit-log row is written without them.

## Open items

There are many openitems, lacunas to be worked upon, below is just a short list from top of our mind -
- **`requested_by` identity:** establish true caller identity for `requested_by` (currently denormalized from session/env config) rather than a config-level default.
- **ERPNext/Frappe MCP tooling:** pending a comprehensive MCP adapter for Frappe/ERPNext — REST connector (`scripts/core/client.py`) is the interim approach.
- **Other ERPs:** extend beyond ERPNext with connector/client handlers for other popular ERPs.
- **Efficiency transparency:** task-level efficiency and token-consumption scoring/visibility.
- **Dynamic LLM selection:** switch model per task at hand (e.g. Haiku for demo-data generation) instead of one fixed `model.default`.
- **Test coverage:** comprehensive testing across skills and connector.
- **Skill/prompt tightening:** make skill instructions and prompts more crisp and robust.

## Reference

- [Profiles: Running Multiple Agents](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
- [Profile Distributions](https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions)
- [Configuration | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/configuration)
- [Personality & SOUL.md](https://hermes-agent.nousresearch.com/docs/user-guide/features/personality)
- [Skills System | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- [Security | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/security)
