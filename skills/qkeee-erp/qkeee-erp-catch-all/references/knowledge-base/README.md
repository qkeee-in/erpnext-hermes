# Knowledge base — convention

This directory is where `qkeee-erp-catch-all` accumulates what it learns
about a specific org's ERPNext instance across sessions, per the module
plan's request that this skill "keep getting wiser and more accurate."
Nothing here is secret — no URLs, API keys, or credentials belong in
these files (those stay in environment variables, per the module plan's
Configuration section). This is purely non-secret research notes:
installed-app inventory, doctype/module mapping, and app-level
documentation summaries.

**Caveat: requires a writable skill mount (added 2026-08-16, adversarial
review).** This design assumes the running harness lets the skill write
back into its own `references/` directory. That does not hold universally
— the reference `erpnext-hermes` Hermes profile in this library mounts
`qkeee-erp-*` skills **read-only** via `skills.external_dirs`
(`erpnext-hermes/profile.md`/`README.md`: "master `qkeee-erp-*` skills
mount read-only ... keeping curated skills separate from this profile's
local/learned skill space"). On a read-only mount, writes under this
directory fail; the skill should catch that failure, tell the user the KB
couldn't be persisted this session (research still happens, it just
won't be remembered next time), and continue rather than blocking the
user's actual request. If your harness needs persistent per-instance
notes and mounts skills read-only, point this skill at a writable
location instead (e.g. the profile's own local/learned skill space or a
workspace directory) rather than assuming `references/knowledge-base/`
is writable — this is a per-deployment decision, not something this
skill can detect and fix generically.

## Why files here, not agent-curated memory (`MEMORY.md`)

The module plan is explicit that ERPNext connection config/state doesn't
belong in agent-curated memory, since that gets injected into every
prompt regardless of relevance. Per-app research notes are the same
shape of problem at a larger scale — an org with several companion apps
installed could accumulate a lot of notes, and only the one or two
relevant to the current request should load into context. Plain files
under this skill's own `references/` directory, read on demand (step 4 of
the investigation method in `domain-knowledge.md`), keep that scoped:
nothing here is auto-loaded into every session.

## Structure

```
knowledge-base/
  <env-tag>/
    _apps.md              # installed-app inventory snapshot for this tag
    <app-name>.md          # one file per app researched for this tag
```

`<env-tag>` matches whatever the user named the environment at connector
setup (e.g. `qa`, `prod`, `client-a-prod`) — the same tag used in
`QKEEE_ERP_<TAG>_*` env vars and `qkeee_erp.active_env`. Different
environments can have different apps/versions installed, so notes are
never shared across tags even if the org is the same.

**Sanitize the tag before using it as a path segment.** Unlike the
env-var form (which uppercases/strips to `[A-Z0-9_]` — see
`erp_client.py`'s `_tag_env_var()`), the raw tag string is user-supplied
free text and must not be used verbatim to build a filesystem path — a
tag containing `/`, `..`, or other path-control characters could escape
`knowledge-base/` entirely. Derive the directory name the same way
`_tag_env_var()` derives the env-var suffix (lowercase alphanumerics and
`-`/`_` only, everything else replaced), so `<env-tag>` in this
convention is always that sanitized form, not the raw user string.

`<app-name>` is the Frappe app's internal name (e.g. `crm`, `hrms`,
`helpdesk`), not its display title — matches what `discover.py apps`/
`modules` reports.

## `_apps.md` template (one per env tag)

```markdown
# Installed apps — <env-tag>

Last confirmed: <date>, via `discover.py apps` (or `modules` fallback).

| App | Version | Notes |
| --- | --- | --- |
| frappe | 15.112.0 | Framework |
| erpnext | 15.112.0 | Core ERP |
| hrms | 15.61.0 | Covered primarily by qkeee-erp-hr-associate |
| crm | 1.57.9 | Not covered by any named persona skill — see crm.md |
```

## `<app-name>.md` template

```markdown
# <app-name> — knowledge base (env: <env-tag>)

Last updated: <date>

## What it is
<one paragraph, from README/docs — what this app does, who it's for>

## Source
<GitHub repo URL if public / upstream Frappe-ecosystem app; "org-specific
custom app, no public repo" otherwise>

## Key doctypes (confirmed live via discover.py, not just documented)
| DocType | Module | Submittable | Custom | Notes |
| --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... |

## Workflows / typical use
<how the doctypes above relate and get used together, from docs +
whatever's been confirmed live>

## API surface (if documented)
<any REST/whitelisted-method endpoints specific to this app, beyond the
generic /api/resource/<DocType> primitives every doctype gets for free>

## Discrepancies between docs and live instance
<anything the docs say that live `meta`/`resolve` output didn't confirm,
or vice versa — flag, don't silently prefer one>

## Open questions
<anything not yet investigated — next session picks up here>
```

Update the relevant file whenever a catch-all session researches
something new about an app that's already in the KB, rather than
creating a duplicate note — append to "Key doctypes" / "Workflows", bump
"Last updated".
