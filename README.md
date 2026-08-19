# erpnext-hermes Agent Profile

Git-tracked source of truth for a Hermes Agent profile specialized as an ERPNext functional consultant and operations agent — running the `qkeee-erp` skill family across HR, Accounts, Inventory, Procurement, Sales, Fixed Assets, System Admin, and MIS reporting.

**Live Hermes profile:** `~/.hermes/profiles/dev-erpnext`
Hermes reads these portable files through symlinks from the live profile directory back into this repo — edit here, not in `~/.hermes/profiles/dev-erpnext` directly.

## What this profile is

An ERPNext specialist that acts like a functional consultant, not a click-executor — it explains the "why" behind a process, detects per-instance customizations before assuming stock ERPNext behavior, and treats every submittable document as requiring human review before it goes live. Full scope boundaries (owns / should-not-own) are defined in [`profile.md`](./profile.md); identity and voice are defined in [`SOUL.md`](./SOUL.md).

## Directory layout

### Tracked and symlinked (edit these, in this repo)

| File / dir | Purpose |
|---|---|
| `distribution.yaml` | Profile manifest / distribution metadata — `distribution_owned` lists exactly what `hermes profile update` is allowed to overwrite (`SOUL.md`, `skills/qkeee-erp/`, `cron/jobs.json`, `config.yaml`) |
| `SOUL.md` | Agent identity, voice, personality — loaded into system prompt slot #1 |
| `config.yaml` | Model, provider, toolsets, `skills.external_dirs`, `skills.write_approval` |
| `mcp.json` | MCP server connections (e.g. ERPNext REST connector) |
| `skills/qkeee-erp/` | Master `qkeee-erp-*` skill family — mounts read-only into the live profile via `skills.external_dirs`, edited here only |
| `plugins/` | Extended functionality modules |
| `cron/` | Scheduled jobs (e.g. recurring MIS reports) |
| `AGENTS.md` | Project-scoped rules, conventions, operational instructions (distinct from `SOUL.md` — see [Personality & SOUL.md](https://hermes-agent.nousresearch.com/docs/user-guide/features/personality)) |
| `CLAUDE.md` | Claude-specific context, if this profile is also driven via Claude Code |
| `profile.md` | Purpose / Owns / Should-Not-Own / safety policy / operating protocol for this agent |

**Skill family** (`skills/qkeee-erp/`):

| Skill | Role |
|---|---|
| `qkeee-erp-frappe-core` | Shared connector, discovery, auth, bot-doctype design — source of truth for `erp_client.py`/`connector-reference.md` |
| `qkeee-erp-bot-init` | Bot user + persona doctype bootstrap |
| `qkeee-erp-accounts-executive` | Accounts persona |
| `qkeee-erp-hr-associate` | HR & Payroll persona |
| `qkeee-erp-inventory` | Inventory persona |
| `qkeee-erp-procurement` | Procurement persona |
| `qkeee-erp-sales` | Sales persona |
| `qkeee-erp-fixed-asset-manager` | Fixed Assets persona |
| `qkeee-erp-mis-analyst` | MIS reporting persona |
| `qkeee-erp-system-admin` | System Admin persona |
| `qkeee-erp-doc-extraction` | Document extraction persona |

Each persona skill's `erp_client.py`/`connector-reference.md` stays synced from `qkeee-erp-frappe-core` via `sync_to_personas.py` — edit the connector logic once in `qkeee-erp-frappe-core`, then re-sync, don't hand-edit the copies.

### Runtime-only (never commit)

`.env`, `auth.json`, `memories/`, `sessions/`, `state.db*`, `logs/`, `workspace/`, `plans/`, `home/`, `local/`, `*_cache/`, `tmp/` — these belong to the live profile instance and can carry secrets or grow to tens of GB. See [Profiles: Running Multiple Agents](https://hermes-agent.nousresearch.com/docs/user-guide/profiles) for what a profile directory holds and why.

### ERPNext credentials: `qkeee-erp.env`, not `.env`

ERPNext instance credentials (`QKEEE_ERP_*`) live in their own file at `$HERMES_HOME/qkeee-erp.env`, deliberately **outside** the profile's main `.env`. `erp_client.py` reads this file directly, bypassing Hermes' sandbox env-stripping (`execute_code`/`terminal` sandboxes strip env vars by default; only statically-declared `required_environment_variables` for the DEFAULT tag survive) and the `env_passthrough` allowlist. This also keeps ERPNext secrets physically separate from any LLM-provider key in the main `.env`.

- Copy `skills/qkeee-erp/qkeee-erp.env.example` to `$HERMES_HOME/qkeee-erp.env` and fill in real values out-of-band — never by having the agent read/cat this file or echo the values back.
- One file holds every environment **tag** (`qkeee_erp.active_env`): `QKEEE_ERP_<TAG>_BASE_URL` / `_API_KEY` / `_API_SECRET` (required), plus optional `_ALLOW_INSECURE`, `_DEBUG`, `_REQUESTED_BY` per tag. Add a new ERPNext instance by appending another tag's trio, never by creating a second file.
- `_DEBUG` is the global debug switch, set per-instance/tag at the env level — not a profile-wide flag — so different environments can run different debug verbosity.
- See `qkeee-erp-frappe-core/SKILL.md`'s "Resolve config" section for the full rationale.
- `_DEBUG` (per tag, e.g. `QKEEE_ERP_DEFAULT_DEBUG`) is the gate for the two high-volume audit doctypes below (`Qkeee Bot Session`, `Qkeee Bot Message`) and for `Read`-action rows in `Qkeee Bot Audit Log`. Leave `false` in normal/production use to avoid bloat; set `true` per-tag on a demo/dev instance when you need full conversation reconstruction for debugging. Write actions (Create/Update/Submit/Cancel/Delete) are logged to Audit Log regardless of this flag — it never gates compliance-critical logging, only the verbose trace.

### Audit-trail doctypes

`qkeee-erp-bot-init` creates 4 `Qkeee Bot *` doctypes directly in ERPNext (no custom app) to give every bot action a compliance-grade trail. Design/rationale: `qkeee-erp-bot-init/references/bot-doctypes-design.md`.

| Doctype | Created | Purpose |
|---|---|---|
| `Qkeee Bot Persona` | Always, once per installed persona skill | Master data — one row per `qkeee-erp-*` persona (code, label, default read/write mode, active flag) |
| `Qkeee Bot Session` | Only when `_DEBUG=true` for the active tag | One row per conversation — user, persona, environment tag, mode, start/end, status |
| `Qkeee Bot Message` | Only when `_DEBUG=true` for the active tag | One row per conversation turn (User/Bot Analysis/Bot Response/Bot Action/System), create-only, linked back to Audit Log on actions that touched ERPNext |
| `Qkeee Bot Audit Log` | **Always** for writes (Create/Update/Submit/Cancel/Delete); `Read` rows only under `_DEBUG=true` | One row per ERPNext record read/written by the bot — action, reference doc, before/after payload, field diff, `user_approved` (Approved/Not Confirmed/Not Required — detection, not a write gate), submittable/locked once resolved |

Outside debug mode, `Qkeee Bot Audit Log.session` still carries the raw session-id string (not a Link) so rows stay correlatable by conversation even with no `Qkeee Bot Session` record backing it.

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
- **Skill source separation:** master `qkeee-erp-*` skills mount via `skills.external_dirs` (read-only), keeping curated skills separate from this profile's local/learned skill space. See [Skills System | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills).
- **Submit-before-review, always:** every docstatus-bearing document requires a review-before-submit step with explicit human confirmation — defined in `profile.md`, enforced at the skill-instruction level in `qkeee-erp-hr-associate`/`qkeee-erp-accounts-executive`/etc.
- **No auth fallbacks:** token auth (`QKEEE_ERP_*` env vars) only — no session-cookie/password workarounds that drop audit attribution.
- **Audit log & tracing:** every ERPNext write goes through `erp_client.py`, which stamps audit-log entries with the acting bot's session id and `_REQUESTED_BY` — no audit-log row is written without both.

## Open items

There are many openitems, lacunas to be worked upon, below is just a short list from top of our mind -
- **`requested_by` identity:** establish true caller identity for `requested_by` (currently denormalized from session/env config) rather than a config-level default.
- **ERPNext/Frappe MCP tooling:** pending a comprehensive MCP adapter for Frappe/ERPNext — REST connector (`erp_client.py`) is the interim approach.
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

Sources:
- Configuration | Hermes Agent
- Security | Hermes Agent
- Skills System | Hermes Agent
- Personality & SOUL.md
- Profiles: Running Multiple Agents
- Profile Distributions

