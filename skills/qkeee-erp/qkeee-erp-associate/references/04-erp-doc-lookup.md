# ERP documentation lookup

Where to find authoritative docs for whatever Frappe/ERPNext package or
app a target environment actually runs — used when live metadata
(`discover.py`, `01-connectivity.md`) tells you *what* a field/doctype is
but not *why*, or when a functional area is unfamiliar. Live metadata
always wins over documentation on a shape question (Non-negotiable 4,
`00-conventions.md`); docs are for behavior/workflow context metadata
can't give you.

## Step 1 — identify what's actually installed

Already part of `02-environment-assessment.md` step 2 — don't re-run it,
reuse the result:

- `discover.py modules` — installed-app inventory (always works).
- `discover.py apps` — same plus version numbers, opportunistic (a
  whitelisted RPC blocked on at least one real instance — fall back to
  `modules` silently).
- If both are unavailable and an exact version matters, ask the user to
  paste ERPNext's own **Help → About** dialog.

Record the result (package/app name + version) in
`qkeee-erp-learned/<env-tag>/references/environment.md` via
`memory_promote.py` — this is exactly what that step already promotes,
so a doc lookup should never need to rediscover it mid-session.

## Step 2 — map app name to doc source

Frappe-ecosystem docs live at predictable per-app subpaths under
`docs.frappe.io`. Convention, not guaranteed for every app — confirm the
page actually exists before citing it:

| Installed app | Docs |
| --- | --- |
| `frappe` (framework itself) | `https://docs.frappe.io/framework/` |
| `erpnext` | `https://docs.frappe.io/erpnext/` |
| `hrms` (Frappe HR) | `https://docs.frappe.io/hr/` |
| `crm` (Frappe CRM) | `https://docs.frappe.io/crm/` |
| `helpdesk` | `https://docs.frappe.io/helpdesk/` |
| `lms` | `https://docs.frappe.io/lms/` |
| `insights` | `https://docs.frappe.io/insights/` |
| `wiki` | `https://docs.frappe.io/wiki/` |
| `drive` | `https://docs.frappe.io/drive/` |
| `gameplan` | `https://docs.frappe.io/gameplan/` |
| `builder` | `https://docs.frappe.io/builder/` |
| `payments` | check `https://docs.frappe.io/payments/` first, fall back to the app's GitHub README (`frappe/payments`) — thinner doc coverage than the others |

For an app not in this table (a companion Frappe-ecosystem app not yet
common enough to list, or anything whose docs subpath 404s): fetch its
GitHub README instead — most live under the `frappe` GitHub org
(`github.com/frappe/<app-slug>`), same as
`02-environment-assessment.md` step 4c already does for an unfamiliar
companion app. Note in the spec/response which source you actually used.

**Version-specific behavior.** `docs.frappe.io` tracks current/latest by
default; where the cataloged version is materially older (a major-version
gap), say so plainly rather than presenting current docs as authoritative
for an old instance — cross-check against live metadata for anything
that looks version-sensitive (a field that docs describe but
`discover.py meta` doesn't show, or vice versa).

## Step 3 — unfamiliar functional area: search, don't guess

For a domain question docs don't answer directly (an edge-case workflow,
"how do other orgs typically handle X in ERPNext"), search
`discuss.frappe.io` (the Frappe community forum) rather than answering
from general LLM knowledge or guessing at ERPNext's intended behavior.
Use whatever web-search/fetch tool this harness exposes
(Non-negotiable 8, `00-conventions.md` — prefer a harness-native tool
over improvising one). Treat a forum answer as community input, not
authoritative the way official docs or live metadata are — say so when
citing one, and prefer a thread that's answered/accepted or from Frappe
staff when more than one result disagrees.

## What this deliberately doesn't try to do

This isn't a general web-research capability grafted onto the associate.
Scope stays: identify the installed package/version, find its official
docs, and fall back to a targeted forum search only when docs genuinely
don't cover the question. A doc/forum finding never overrides live
metadata or an explicit statement from the user (Non-negotiable 4) — it
fills in *why*, never *what's actually there*.
