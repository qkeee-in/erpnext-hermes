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
| `distribution.yaml` | Profile manifest / distribution metadata |
| `SOUL.md` | Agent identity, voice, personality — loaded into system prompt slot #1 |
| `config.yaml` | Model, provider, toolsets, `skills.external_dirs`, `skills.write_approval` |
| `mcp.json` | MCP server connections (e.g. ERPNext REST connector) |
| `skills/` | Local skill overrides — reserved for dynamic/learned skills only; master `qkeee-erp-*` skills mount read-only via `skills.external_dirs`, not here |
| `plugins/` | Extended functionality modules |
| `cron/` | Scheduled jobs (e.g. recurring MIS reports) |
| `AGENTS.md` | Project-scoped rules, conventions, operational instructions (distinct from `SOUL.md` — see [Personality & SOUL.md](https://hermes-agent.nousresearch.com/docs/user-guide/features/personality)) |
| `CLAUDE.md` | Claude-specific context, if this profile is also driven via Claude Code |
| `profile.md` | Purpose / Owns / Should-Not-Own / safety policy / operating protocol for this agent |

### Runtime-only (never commit)

`.env`, `auth.json`, `memories/`, `sessions/`, `state.db*`, `logs/`, `workspace/`, `plans/`, `home/`, `local/`, `*_cache/`, `tmp/` — these belong to the live profile instance and can carry secrets or grow to tens of GB. See [Profiles: Running Multiple Agents](https://hermes-agent.nousresearch.com/docs/user-guide/profiles) for what a profile directory holds and why.

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

## Safety & governance

- **Skill write approval:** `skills.write_approval: true` in `config.yaml` stages every agent-initiated skill write (create/edit/patch/delete) under `~/.hermes/pending/skills/` for approve/deny review via `/skills pending`, `/skills diff`, `/skills approve`, `/skills reject` — nothing lands unreviewed. See [Security | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/security).
- **Skill source separation:** master `qkeee-erp-*` skills mount via `skills.external_dirs` (read-only), keeping curated skills separate from this profile's local/learned skill space. See [Skills System | Hermes Agent](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills).
- **Submit-before-review, always:** every docstatus-bearing document requires a review-before-submit step with explicit human confirmation — defined in `profile.md`, enforced at the skill-instruction level in `qkeee-erp-hr-associate`/`qkeee-erp-accounts-executive`/etc.
- **No auth fallbacks:** token auth (`QKEEE_ERP_*` env vars) only — no session-cookie/password workarounds that drop audit attribution.

## Reference

- [Profiles: Running Multiple Agents](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
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

