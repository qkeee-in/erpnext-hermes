# Environment assessment procedure

Runs once per environment tag, the first time this skill talks to that tag
(see the activation sequence in `SKILL.md`), and again whenever something
about the target instance looks different from what durable memory says (a
version bump, an app that wasn't there before).

This document is the *procedure* — what to check and in what order. It is
NOT the memory mechanism itself: that's `scripts/core/memory_promote.py`
(redact + format, then hand off to Hermes' `skill_manage` tool) writing
into `qkeee-erp-learned/<env-tag>`, per step 6 below. Don't invent a
bespoke file-write step to fill that gap — use `memory_promote.py` and
Hermes' native memory tools.

## When this procedure runs

- **First contact with a tag.** Per the activation sequence: resolve the
  env tag, run `health`, then check whether a `qkeee-erp-learned/<env-tag>`
  skill already exists (Hermes' own skill discovery surfaces it if so). If
  it exists, latch it like any other reference and skip straight to intent
  classification — don't re-run this procedure against an environment
  already cataloged, unless something looks stale (see below).
- **No `qkeee-erp-learned/<env-tag>` skill found.** Run the full procedure
  below before doing anything substantive, then promote the findings into
  one (step 6).
- **Staleness signal mid-session.** A `discover.py apps` version that
  doesn't match what durable memory says, an app installed/removed since
  last cataloged, or a doctype `meta` call that disagrees with what's
  recorded — re-run the relevant step and update, don't silently trust
  the stale note over live data. Live metadata always wins over a prior
  session's memory.

## Procedure

1. **Health check.** `core/client.py --tag <tag> health` — confirms
   connectivity + auth, not query/write-time permission. Report a later
   permission error as its own distinct failure mode, never lumped in
   with a connectivity failure.
2. **Installed apps + versions.** `discover.py modules` first (plain REST
   read, broadly confirmed working), `discover.py apps` as a bonus for
   version numbers `modules` can't derive — treat `apps` as opportunistic
   per `01-connectivity.md`'s note on its confirmed-blocked RPC method on
   at least one real instance. If both are needed and `apps` fails, fall
   back to `modules` silently and ask the user to paste the Help → About
   dialog only if exact versions genuinely matter.
3. **Which domains apply here.** Cross-reference the installed-app list
   against the domain table in `SKILL.md` — a companion app (Frappe CRM,
   Helpdesk, LMS, Insights, Wiki, Drive, Gameplan, Builder, Payments, or
   an org-specific custom app) that isn't covered by any of the eleven
   fixed domain slugs is fallback-investigation territory: catalog it the
   same way as a custom app (see `non-erpnext-adapter.md`'s catalog
   convention, which this procedure shares), never silently ignore it or
   silently invent a twelfth domain slug to cover it.
4. **For an unfamiliar doctype or custom app, apply the investigation
   method** (what a seasoned ERPNext/Frappe SME actually does, in order):
   a. `discover.py resolve "<DocType>"` — module + owning app +
      submittable/custom flags. Confirms whether this is core, a
      companion app, or genuinely custom before researching further.
   b. `discover.py meta "<DocType>"` — live field list, mandatory flags,
      Link targets. Never propose a shape without this.
   c. For a companion Frappe-ecosystem app: fetch its GitHub README/docs
      (most live under the `frappe` GitHub org) for what it's for, its key
      doctypes, and typical workflows — cross-check against the live
      metadata from step (b); note any discrepancy rather than silently
      preferring one source.
   d. For a genuinely org-specific custom app with no public repo: build
      the understanding from live metadata + whatever the user explains,
      and say so explicitly rather than inventing an upstream source.
5. **Cross-check the requester's identity.** Per the activation
   sequence's step 2 (see `SKILL.md`) — this happens on every session,
   not just first contact, but first contact is where "is this bot account
   even a dedicated service identity, not someone's personal login" also
   gets its first check (see `00-conventions.md`'s GRC baseline).
6. **Record what was found.** Run `memory_promote.py` (or call its
   `build_promotion_plan()` directly): redact, format, and promote
   Frappe/ERPNext/app versions, the custom doctype catalog, and any
   non-ERPNext system notes into `qkeee-erp-learned/<env-tag>`'s
   references (`environment.md`, `doctypes-catalog.md`,
   `custom-apps/<slug>.md`, `non-erpnext/<slug>.md` — see
   `00-conventions.md`'s naming table), plus a one-line breadcrumb in
   `<profile>/memories/MEMORY.md` naming the environment tag and pointing
   at the full skill. `memory_promote.py` cannot issue the `skill_manage`/
   `memory` tool calls itself (it runs as a subprocess script — see its
   own docstring); issue the calls it returns yourself, in order, and stop
   at the first failure.

## What this deliberately doesn't try to do

This is not a general-purpose Frappe-app auditor, and it doesn't attempt
to reverse-engineer business logic behind a custom app's
doctypes beyond what live metadata and the user's own explanation cover.
If a specific investigated capability becomes trusted and repeatedly used,
that's a signal it deserves a proper domain reference of its own (a
twelfth domain slug, deliberately added to the enum — see
`00-conventions.md`), not a reason to grow this procedure's own scope
indefinitely.
