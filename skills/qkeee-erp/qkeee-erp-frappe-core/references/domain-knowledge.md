# qkeee-erp-frappe-core — domain knowledge (fallback-investigation mode)

This is the ERP-agnostic half of the split (per the module plan's
Architecture decision) for this skill's OWN fallback-investigation
identity (merged in from the former `qkeee-erp-catch-all` skill,
2026-08-18) — the *method* a seasoned ERPNext/Frappe SME uses to get
productive on a doctype, module, or app they haven't worked with before,
independent of which specific app happens to be installed on a given
org's instance. `references/connector-reference.md` + `scripts/discover.py`/
`erp_client.py` are the technical layer this method executes through.

This file covers only the fallback-investigation mode. This skill's OTHER
identity — the canonical connector substrate every persona skill copies
from — has no domain knowledge of its own; see `SKILL.md` and
`references/connector-reference.md` for that side.

## When this skill's fallback mode is the right one

The eight named `qkeee-erp-*` persona skills each own a named functional
area (HR, Accounts, Fixed Assets, System Admin, Procurement, Sales,
Inventory, MIS/Reporting) with hand-built domain knowledge and capability
tables. This skill's fallback mode exists for everything *outside* that —
check the routing table below first; if the user's request clearly maps
to one of the eight, say so and hand off rather than reinventing coverage
that already exists in a more expert-tuned skill.

### Routing table — defer to the named persona skill when the request is about:

| Persona skill | Core doctypes it owns |
| --- | --- |
| `qkeee-erp-hr-associate` | Employee, Leave Application, Attendance, Job Opening, Job Applicant, Interview, Offer Letter, Employee Onboarding/Separation |
| `qkeee-erp-accounts-executive` | Journal Entry, Payment Entry, Sales/Purchase Invoice, Expense Claim, GST/TDS-related doctypes |
| `qkeee-erp-fixed-asset-manager` | Asset, Asset Category, Asset Movement, Asset Maintenance, Asset Repair |
| `qkeee-erp-system-admin` | User, Role, Role Profile, Workflow, Custom Field, Print Format, Notification |
| `qkeee-erp-procurement` | Supplier, Purchase Order, Request for Quotation, Supplier Quotation, Purchase Receipt |
| `qkeee-erp-sales` | Customer, Quotation, Sales Order, Delivery Note |
| `qkeee-erp-inventory` | Item, Warehouse, Stock Entry, Stock Reconciliation, Material Request, Batch, Serial No |
| `qkeee-erp-mis-analyst` | GL Entry, Trial Balance/P&L/Balance Sheet reports, Cost Center, Accounting Dimension |

### This skill's fallback mode is the right one when:

- A doctype/feature belongs to a **companion Frappe app** the org has
  installed beyond core `frappe`+`erpnext` — e.g. Frappe CRM (`crm`),
  Frappe HR beyond what `qkeee-erp-hr-associate` covers (`hrms`),
  Helpdesk (`helpdesk`), LMS (`lms`), Insights (`insights`), Wiki
  (`wiki`), Drive (`drive`), Gameplan (`gameplan`), Builder (`builder`),
  Payments (`payments`), or any org-specific custom app.
- A doctype is a **custom DocType** (`custom: 1` in its meta) built for
  this org specifically, with no equivalent in any named persona skill.
- The user names a doctype/feature you don't recognize and it isn't
  clearly one of the eight named modules — investigate first, don't
  guess or refuse.

## The investigation method (what a seasoned ERPNext/Frappe SME actually does)

Applied in this order, every time an unfamiliar doctype or feature comes
up. Skipping straight to "let me write a payload" without this is how a
bot ends up guessing field names that don't exist on this instance.

1. **Check the routing table above first.** If it's clearly one of the
   eight named areas, say so and point the user there instead of
   duplicating coverage.

2. **Resolve the doctype's metadata.** `discover.py resolve <DocType>`
   (or `meta` for the full live field list) — this is the single most
   important step. It tells you the doctype's owning `module`, which
   `app` that module belongs to, whether it's a child table
   (`istable`), whether it's submittable, whether it's custom, and its
   actual field list with mandatory flags and Link targets **as they
   exist on this specific instance right now** — not as documented
   generically. Never propose a field that isn't in this output.
   Requires System Manager–level read access to the `DocType` doctype
   (`GET /api/resource/DocType/<name>`) — this can 403 under a
   correctly least-privileged shared bot account (see the module plan's
   bot-init least-privilege decision), which is a permissions gap to
   surface to the user, not a sign the doctype doesn't exist.

   `resolve`'s `app` field can be `null` for two different reasons —
   don't conflate them. `app_lookup_error: null` alongside `app: null`
   means there was genuinely no `module` to trace (nothing to look up).
   A non-null `app_lookup_error` means the Module Def lookup itself
   failed (permission, network, a module name that didn't resolve) —
   report that as "couldn't confirm the owning app" to the user, not as
   "this doctype has no owning app / is custom."

3. **Enumerate installed apps and their versions.** Try `discover.py
   modules` first — a plain REST read (`Module Def` rows) that's
   confirmed working. `discover.py apps` (About-dialog data — Frappe
   Framework, ERPNext, and any companion apps each with their own
   version line) is worth trying too since `modules` alone can't produce
   version numbers, but treat it as opportunistic, not the primary path:
   its whitelisted RPC method (`frappe.utils.change_log.get_versions`)
   was confirmed blocked (`PermissionError: not whitelisted`) on a real
   instance during this skill's own build-time validation
   (`demo.qkeee.in`) — that failure is an expected, common
   outcome on a hardened instance, not a rare version mismatch. If both
   fail (or exact version numbers matter and `modules` alone doesn't
   answer that), ask the user to paste the Help → About dialog contents
   directly (screenshot or text) — never guess a version.

4. **Check the knowledge base for this environment tag + app.**
   `references/knowledge-base/<env-tag>/<app-name>.md` — if a prior
   session already researched this app, read it first rather than
   re-researching from scratch. See `references/knowledge-base/README.md`
   for the file convention.

5. **If the app/doctype is new to the knowledge base, research it.**
   For a known Frappe-ecosystem app (`frappe/<app>` on GitHub is the
   first guess for anything under the `frappe` GitHub org — CRM, HR,
   Helpdesk, LMS, Insights, Wiki, Drive, Gameplan, Builder, Payments all
   live there), fetch the repo's README and any `docs/`/documentation
   site for: what the app is for, its key doctypes and how they relate,
   its own REST/API surface if documented, and typical workflows. For an
   org-specific custom app, there may be no public repo — rely on live
   `meta`/`resolve` output plus whatever the user tells you about intent.
   **Docs describe general shape; live metadata (step 2) is ground
   truth for this specific org's instance** — when they disagree
   (a documented field that live meta doesn't show, or vice versa),
   trust live meta and note the discrepancy in the KB entry.

6. **Write (or update) the knowledge-base entry** for this app under
   this environment tag before doing anything else with it — see the KB
   README for the template. This is the "keep getting wiser" mechanism:
   next time this org's instance comes up, step 4 finds it immediately
   instead of re-researching.

7. **Only then propose a plan to the user**, grounded in what steps 2–6
   actually confirmed exists — the live field list, the mandatory
   fields, the Link targets, and (from the KB) the app's intended
   workflow. State clearly which parts are confirmed-live vs.
   documentation-derived vs. still uncertain; don't present a guess with
   the same confidence as a confirmed field.

## Extra caution beyond the standard six-stage workflow pattern

The module plan's six-stage pattern (Intake → Validate → Stage/Draft →
Confirm → Execute → Report back) still applies to every write this skill
performs — see the module plan for the full definition. This skill's
fallback mode adds one thing on top, because unlike the eight named
personas it has no pre-vetted, human-reviewed capability table for
whatever doctype comes up:

- **Every write-capable capability here is advisory-first by default,
  enforced in code — not just a prompt-level default.** Because the
  doctype wasn't scoped and reviewed at design time the way each persona
  skill's capability table was, this skill always renders the draft via
  `scripts/render_draft.py` (payload, which fields it resolved from live
  meta vs. which it's inferring from the user's request, and a
  `confirmation_token`/`issued_at`) and gets the user's explicit
  go-ahead, before calling `erp_client.gated_mutate_resource()` with that
  token — even in `read-write` mode. `gated_mutate_resource()` recomputes
  the token from the actual call and refuses a missing/stale/mismatched
  one, so there is no code path in this skill's `erp_client.py` that
  writes without a matching rendered draft first. Live-write authority
  for a specific, well-understood fallback-mode capability can be
  promoted out of this advisory-first default once a human has actually
  reviewed and trusted it repeatedly — at that point it arguably belongs
  in a proper persona skill (or a new one, calling `mutate_resource()`
  directly like the named personas do) rather than staying in this
  skill's catch-all mode.
- **Never invent a field name.** If the user asks for something a field
  list from step 2 doesn't show, say so explicitly rather than guessing
  a plausible-sounding fieldname — a wrong Link-field guess that happens
  to resolve to some unrelated existing record is worse than an honest
  "I don't see that field on this DocType."
- **Cross-check every Link field against a live query**, same as the
  save-draft-then-review-then-submit discipline every other persona skill
  follows (see `references/connector-reference.md`) — doubly important
  here since the doctype hasn't been vetted before.

## What this skill's fallback mode deliberately doesn't try to do

- It doesn't replace a properly built persona skill. If a fallback-mode
  investigation turns into repeated, trusted usage of some doctype/app,
  that's a signal to build (or extend) a real persona skill for it, not
  to keep growing this skill into a tenth monolith.
- It doesn't assume every unfamiliar app is a Frappe-ecosystem app on
  GitHub — a genuinely org-specific custom app/doctype has no upstream
  docs to fetch, and step 5 above degrades to "live metadata + what the
  user tells you" in that case, which is fine.
