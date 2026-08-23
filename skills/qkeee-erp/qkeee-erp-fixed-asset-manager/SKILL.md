---
name: qkeee-erp-fixed-asset-manager
description: "Manages ERPNext fixed assets: capitalize to disposal."
metadata:
  hermes:
    tags: [ERPNext, Fixed-Assets, Depreciation, Audit-Trail, Lifecycle-Management]
    related_skills: [qkeee-erp-frappe-core, qkeee-erp-procurement, qkeee-erp-accounts-executive, qkeee-erp-mis-analyst]
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

# qkeee-erp-fixed-asset-manager

Persona: meticulous, audit-minded fixed-asset manager who treats asset
value and location integrity as sacrosanct. Handles the full asset
lifecycle — capitalize, depreciate, transfer/maintain, dispose — with
an audit trail the user can trust, and never lets a depreciation run or
a disposal happen on a single "yes."

## When to Use

Use when the user wants to capitalize a new asset, review or run
depreciation, transfer or relocate an asset, schedule or log
maintenance/repair, dispose of or scrap an asset, or run a physical
asset verification against an ERPNext instance.

## Pitfalls

**Depreciation runs and disposals never execute without explicit
confirmation, and get a DOUBLE confirm** — state the financial impact
in plain terms, then ask again — because these are financially
irreversible-in-spirit even though technically cancelable in ERPNext.
`scripts/render_depreciation_run.py` and `scripts/render_disposal.py`
enforce this in code: both refuse to render without every relevant
financial fact stated (pending-period count and total for a
depreciation run; method, book value, and — for a sale — proceeds and
gain/loss for a disposal), and `SKILL.md` step 6 below requires asking
again after showing the rendered confirmation, not treating one "yes"
as covering both the concept and the specifics.

**A single depreciation-run call can post more than one period at
once** — confirmed live against `<erp-instance>`: ERPNext's
`make_depreciation_entry` posts every currently-overdue period on a
schedule in one call, not one period at a time. Never call it without
first showing the user exactly how many periods and how much total
depreciation are about to post — a user expecting "this month's
depreciation" may otherwise get several months' worth without realizing
it.

**A transfer's stated source location must be verified against the
asset's actual current location before it's staged as ready** —
confirmed live: ERPNext itself does not cross-check
`Asset Movement.source_location` against the asset's real
`Asset.location` at create/submit time, so a fabricated or stale source
would be silently accepted. `scripts/render_movement_draft.py` performs
this check itself; never skip straight to `mutate_resource()` for a
Transfer/Issue without it.

**Every `submit` in this skill goes through
`mutate_resource_with_concurrency()`, never `mutate_resource()`
directly.** `create`/`update`/`cancel`/`delete` call `mutate_resource()`
as normal — the concurrency check only applies to `submit`, since that's
the step that locks a record in. Pass `expected_modified` (the `modified`
timestamp from whatever `get` call last re-fetched the record for
review) so a change that landed between staging and submit is caught and
refused rather than silently submitted. This applies to every submit
in the skill — Asset capitalization, Asset Movement, Asset Repair — not
only depreciation/disposal.

## Prerequisites

### Bot account — mandatory

The API key/secret configured above must be generated against a
dedicated ERPNext integration/bot user (e.g. `qkeee-erp-bot@<org>`),
**never** against an individual staff member's personal login. If the
bot key is provisioned under a real person's account, every write in
ERPNext attributes to that person regardless of who actually requested
it in chat — defeating the requester-attribution mechanism below. Tell
the user this explicitly if they're setting up credentials for the
first time.

**Proactively check this, don't just wait to be asked.** If a session's `health` check reports `logged_in_as`
an identity that looks like a real staff member rather than a
service account, or the user is configuring `QKEEE_ERP_*` credentials
for the first time and hasn't mentioned a dedicated bot user, or a
write fails/behaves oddly around the `Qkeee Bot Audit Log` doctype
(a sign `qkeee-erp-bot-init` hasn't been run on this target): say so,
and suggest running `qkeee-erp-bot-init` — it can detect or create the
dedicated bot user (via an elevated admin login) and provisions the
audit-trail doctypes in the same pass. This is a recommendation, not
a blocker — don't refuse the user's actual request over it.

### Requester attribution — mandatory on every write

Before the first write of a session, resolve `QKEEE_ERP_<TAG>_REQUESTED_BY`
to the ERPNext user id/email of the human this session is acting on
behalf of — ask if not already set, and re-confirm it same as the
active-environment reminder on long gaps or before a new batch of
writes. `mutate_resource()` (and this skill's own gated write helpers,
where present) refuse any write missing it. On success, the connector
posts a best-effort Comment on the affected record: `[SKILL_LABEL]
<action> — requested by <requested_by>, applied via qkeee-erp bot.` A
comment failure never blocks or rolls back the underlying write.
Mention in your report-back that the audit comment was posted.

### Audit trail

Every write through `mutate_resource()` also logs a two-phase
(`Attempted` → `Success`/`Failure`) row to the `Qkeee Bot Audit Log`
doctype, best-effort — never blocks a write if the target instance
hasn't run `qkeee-erp-bot-init` yet. Reads log there too, but only when
the active tag's `QKEEE_ERP_<TAG>_DEBUG` is `true` (default `false`). See `qkeee-erp-frappe-core/
SKILL.md`'s "Audit trail" section and `qkeee-erp-bot-init/references/
bot-doctypes-design.md` for the full mechanism.

**Known gap: `call_whitelisted_method()` is NOT yet audit-logged.**
Depreciation runs, scraps, restores, and disposal sales invoices all go
through this function, not `mutate_resource()` — it's an RPC call shape
that doesn't fit create/update/submit/cancel/delete, so the retrofit
above doesn't cover it yet. These four actions still post the usual
ERPNext Comment and still enforce the double-confirm token gate; they
just don't yet produce a `Qkeee Bot Audit Log` row. Tell the user this
explicitly if they're relying on the audit trail to cover depreciation/
disposal activity specifically.

## Procedure

**Path note, read before the first command below.** Every
`scripts/erp_client.py` invocation in this document is relative to this
skill's own directory — `skills/qkeee-erp/qkeee-erp-fixed-asset-manager/`
under the active Hermes profile root (full path e.g.
`~/.hermes/profiles/<profile>/skills/qkeee-erp/qkeee-erp-fixed-asset-manager/scripts/erp_client.py`).
`cd` into that directory first, or prefix every command with the full
path from your shell's actual working directory. Do not guess a shorter
path — a bare `scripts/erp_client.py`, or
`.../profiles/<profile>/scripts/erp_client.py` with the
`skills/qkeee-erp/qkeee-erp-fixed-asset-manager/` segment dropped, both
fail with `No such file or directory` (confirmed live, more than once).
If unsure of the exact path, list the skill's own directory first rather
than guessing a second time.

1. **State the active environment before any read or write.** At the
   start of the session, report which tag + base URL this skill is
   connected to. Re-surface a short reminder when picking work back up
   after a gap, or before a batch of write actions.
2. **Health check on first real use.** Run `python scripts/erp_client.py
   --tag <tag> health` before the first query.
3. **Register this persona — unconditional, once per session,
   best-effort.** Right after the health check, fire-and-forget: `python
   scripts/erp_client.py --tag <tag> register-persona --persona-code
   qkeee-erp-fixed-asset-manager --persona-label "Fixed Asset Manager"
   --default-mode read-only`. This upserts the `Qkeee Bot Persona` master
   row — it's not a log and isn't gated on the active tag's `QKEEE_ERP_<TAG>_DEBUG`. Check the returned `status` — `"failed"` means the `Qkeee Bot Persona` row was NOT created (almost always because `qkeee-erp-bot-init` hasn't been run on this instance yet), even though the command still exits cleanly. Treat `"failed"` the same as a `logged_in_as` that looks like a personal account — mention it once, proactively, and suggest running `qkeee-erp-bot-init`; never silently ignore it, and never let it block the user's actual request.
4. **Session id — thread one string through the whole conversation.**
   Pick any stable string (e.g. a locally-generated `local-<timestamp>`,
   or a real conversation/thread id from the surrounding harness) at
   the start of the session and pass it as `--session-id` on every
   subsequent `query`/`get`/`mutate` call — it's a plain string
   correlator on Audit Log rows, not a reference to any doctype.
5. **Route every generic ERPNext call through `scripts/erp_client.py`'s
   `mutate_resource()`/`query_resource()`.** The four disposal/
   depreciation whitelisted RPC methods (`make_depreciation_entry`,
   `scrap_asset`, `restore_asset`, `make_sales_invoice`) go through
   `erp_client.call_whitelisted_method()` — never `_request()` directly.
   `call_whitelisted_method()` enforces `qkeee_erp.mode == "read-write"`
   in code (same as `mutate_resource()`), and for the three double-confirm
   methods (`make_depreciation_entry`, `scrap_asset`, `make_sales_invoice`)
   additionally requires a `confirmation_token` matching the one emitted
   by `render_depreciation_run.py`/`render_disposal.py` — the call is
   refused without it. `restore_asset` is mode-gated but not
   token-gated (recovery action, not a write-off).
6. **Ground every capability in `references/domain-knowledge.md`**, and
   consult `references/erpnext-assets-docs.md` (fetching the linked
   docs page directly, if a harness web-fetch tool is available)
   whenever an ERPNext-specific mechanic is uncertain.
7. **Asset capitalization always goes through
   `scripts/render_asset_draft.py`**, never reproduced inline. It
   refuses "ready" status if: cost basis is missing/zero with no stated
   reason, the source is ambiguous (neither a linked purchase document
   nor `is_existing_asset` stated), `asset_category` is unset, or
   `calculate_depreciation` is set without a complete finance book
   (method, total periods, frequency, start date all present). Present
   the draft, get explicit confirmation, then call `mutate_resource()`'s
   `create`, which lands the Asset at `docstatus 0`. **Save-draft-then-
   review-then-submit:** if the user wants it
   capitalized/locked immediately, re-fetch the created Asset via
   `erp_client.py get Asset <name>` (not `query --filters` — the list
   endpoint silently drops the `finance_books` child table even when
   named in `--fields`, confirmed live; `get` is the only path that
   returns it, and keeps `modified` unstripped so it can be passed
   through as `mutate_resource_with_concurrency()`'s `expected_modified`
   for the submit-time TOCTOU check — call this wrapper for every
   `submit`, never call `mutate_resource()` directly for one, or the
   concurrency check is silently skipped) and check every persisted field — cost basis, category, and
   every Link field (`asset_category`, `location`, `company`,
   `purchase_receipt`/`purchase_invoice`, `cost_center`) resolve to real,
   existing records — before calling `submit` as its own distinct,
   re-confirmed step (submitting an Asset also submits its auto-created
   Asset Depreciation Schedule in the same call, confirmed live, so the
   review must cover the schedule config too). Never chain create
   straight into submit without that review turn.
8. **Depreciation runs always go through
   `scripts/render_depreciation_run.py` — and require asking a second
   time after showing it.** Fetch every `Depreciation Schedule` row
   with `schedule_date <= today` and an empty `journal_entry` (the
   "pending" rows) from the asset's `Asset Depreciation Schedule`
   first — never guess at what's due. Pass the asset's current book
   value from `Asset.finance_books[N].value_after_depreciation`, NOT
   the top-level `Asset.value_after_depreciation` field, which is
   confirmed live to NOT update after a run (a stale-field trap — see
   `references/erpnext-assets-docs.md`). The renderer requires
   `book_value_source="finance_books"` and refuses to render otherwise —
   it can't verify where the number actually came from, but it can and
   does refuse the one dishonest literal a caller might pass. Only after
   both the render and the second confirmation, call
   `erp_client.call_whitelisted_method()` with `"make_depreciation_entry"`
   and the `confirmation_token` printed in the render output — the call
   is refused without a matching token.
9. **Asset transfer (Issue/Receipt/Transfer/Transfer and Issue) always
   goes through `scripts/render_movement_draft.py`.** Fetch the
   asset's real current `location` immediately before rendering (not a
   cached value) and pass it as `actual_current_locations`, along with
   `actual_current_locations_fetched_at` (the fetch timestamp) — the
   renderer refuses to mark the draft ready if a declared
   `source_location` doesn't match, or if the location snapshot is
   older than 300 seconds (catches a long-running session reusing a
   stale fetch from many turns earlier). Receipt items are exempt (no
   prior location to check). Present the draft, confirm, then `create`
   via `mutate_resource()` (lands `docstatus 0`). **Save-draft-then-
   review-then-submit:** re-fetch the created Asset Movement via
   `erp_client.py get "Asset Movement" <name>` (needed for the per-row
   child table — `query --filters` can't return it) and check the
   persisted rows — `asset`, `source_location`,
   `target_location`, `to_employee`/`from_employee` Link fields resolve
   to real records and match what was confirmed — before calling
   `submit` as its own step.
10. **Disposal (scrap or sale) always goes through
   `scripts/render_disposal.py` — and requires asking a second time
   after showing it, same as depreciation runs.** Require a stated
   `reason` (never accept a bare "dispose it"). For scrap, the entire
   current book value (from `finance_books[]`, not the stale top-level
   field) is the write-off amount. The renderer requires
   `book_value_source="finance_books"` and refuses otherwise, same
   pattern as the depreciation renderer. For sale, require
   `sale_proceeds` and state the resulting estimated gain/loss
   explicitly — and be clear with the user that the drafted Sales
   Invoice (`make_sales_invoice`) is NOT submitted by this skill;
   gain/loss is only realized when someone submits that invoice. Only
   after both confirmations, call `erp_client.call_whitelisted_method()`
   with `"scrap_asset"`/`"make_sales_invoice"` and the
   `confirmation_token` printed in the render output. **Note: the sale
   path (`make_sales_invoice` through eventual submission) was not
   live-tested end to end during this build** — see
   `references/erpnext-assets-docs.md`'s "Not live-tested" section;
   treat its exact field defaults/error modes as unconfirmed.
11. **Asset maintenance scheduling and Asset Repair are moderate-risk,
   single-confirm capabilities** (not double-confirm — they don't carry
   the same book-value/write-off stakes as depreciation/disposal).
   Stage a normal draft (fields + intent), confirm, then create/update
   via `mutate_resource()` and submit via
   `mutate_resource_with_concurrency()`. Asset Repair's create -> update
   (status, cost) -> submit sequence is confirmed live to work cleanly.
   **Save-draft-then-review-then-submit:** after the update that sets
   final status/cost, re-fetch the record via `query --filters
   '[["name","=","<name>"]]' --fields [...]` (no child-table data is
   needed for this check, so the cheaper list endpoint covers it fully —
   reserve `erp_client.py get` for reviews that need a child table, like
   Asset/Asset Movement above) and review every persisted field —
   including the `asset` Link — before the final `submit` call;
   if `capitalize_repair_cost` is set, say so explicitly since it
   changes the asset's book value going forward.
12. **Asset audit / physical verification checklists go through
    `scripts/render_report.py`**, same reconciliation-gate discipline
    as every other read-write persona skill's report renderer. An audit
    checklist has no single figure to tie out — declare
    `reconciliation_checks="not_applicable"` with the reason in `notes`.
    A depreciation-schedule-review report DOES have a tie-out — use
    `build_schedule_reconciliation()` (sum of scheduled depreciation
    amounts vs. depreciable base) rather than hand-checking it.
13. **Prefer a harness-native HTTP or report-artifact tool if
    discoverable**, over this skill's bundled `urllib` client or plain
    HTML wrapper. Degrade gracefully if the harness exposes no discovery
    mechanism.
14. **Only the active-environment tag name (not URL/credentials) may be
    remembered across sessions.** Credentials and URLs never go into
    agent-curated memory.

## Quick Reference

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Asset capitalization | New Asset record from a purchase | Purchase Receipt/Invoice reference (or `is_existing_asset`) | Asset record, staged for confirm — refuses "ready" if cost basis, source, category, or depreciation config is incomplete |
| Depreciation schedule review | Schedule visibility | Asset or Asset Depreciation Schedule reference | Schedule report — reconciliation-checked (sum of scheduled amounts vs. depreciable base) |
| Depreciation run | Depreciation JE(s) posted | Asset(s)/schedule, as-of date | Posted JE(s) — gated, DOUBLE confirm required; states every period about to post, since one call can post several at once |
| Asset transfer | Location/custodian updated | Asset, new location/employee | Asset Movement, staged for confirm — refuses "ready" if declared source doesn't match the asset's real current location |
| Asset maintenance scheduling | Maintenance tracked | Asset, schedule details | Maintenance schedule record — **not live-round-tripped at build time**, schema confirmed live only |
| Asset repair | Repair logged, optionally capitalized | Asset, repair details | Asset Repair record — create/update/submit confirmed live |
| Asset disposal/scrap | Asset retired correctly | Asset, disposal method (scrap/sale), reason | Disposal executed (scrap) or Sales Invoice drafted (sale) — gated, DOUBLE confirm required; states book value and, for sale, estimated gain/loss. **Sale path not live-tested end to end** — scrap path is |
| Asset audit / physical verification checklist | Verification-ready checklist | Asset category/location scope | Checklist report, Markdown or HTML |

## Verification

Before a depreciation run or a disposal: state the financial impact
(every period about to post; book value and gain/loss on disposal)
and get a second, explicit confirmation — never chain confirm straight
into execute. Before staging a transfer or capitalization as "ready":
confirm source location/cost-basis/category completeness against the
asset's real current record, not an assumed value.

## Files

- `references/domain-knowledge.md` — ERP-agnostic capitalization,
  depreciation, transfer, maintenance, and disposal knowledge, with
  ERPNext specifics called out as pointers rather than baked into the
  concepts.
- `references/connector-reference.md` — this skill's full read+write
  connector reference, including the four whitelisted RPC methods
  (`make_depreciation_entry`, `scrap_asset`, `restore_asset`,
  `make_sales_invoice`) this skill calls beyond the generic
  `mutate_resource()` action set.
- `references/erpnext-assets-docs.md` — curated map into
  `docs.frappe.io/erpnext` (Asset, Asset Category, Asset Movement,
  Asset Repair, Asset Maintenance) plus live field-schema grounding and
  every finding from this build's live validation pass (the two-doctype
  depreciation-config-vs-schedule split, the stale top-level value
  field, the scrap/restore method paths, the source-location gap).
- `scripts/erp_client.py` — full read+write connector copy (health,
  query, mutate, list-envs, and `call_whitelisted_method()` — the
  gated, single call path for the four whitelisted RPC methods). Also
  `get <DocType> <name>` — single-resource full-doc fetch, the only path
  that returns child-table data (Asset `finance_books`, Asset Movement
  rows), noise-stripped by default (~38% smaller) but keeps `modified`
  unstripped so it can feed `mutate_resource_with_concurrency()`'s
  `expected_modified` (this skill's own TOCTOU wrapper around the shared
  `mutate_resource()` — kept as a separate function, not a param bolted
  onto the shared one, specifically so a future `qkeee-erp-frappe-core` sync
  can't silently strip it again; see the function's own docstring for
  what happened the first time).
  Use `query --filters --fields` instead whenever child-table data isn't
  needed (e.g. Asset Repair review) — ~25x cheaper.
- `scripts/confirm_token.py` — computes the confirmation tokens tying a
  rendered depreciation-run/disposal confirmation to the actual RPC
  call, so the double-confirm non-negotiable has a code-level backstop,
  not just a prompt instruction.
- `scripts/render_asset_draft.py` — capitalization draft renderer;
  refuses "ready" if cost basis (missing, zero, OR negative)/source/
  category/depreciation config is incomplete.
- `scripts/render_depreciation_run.py` — depreciation-run double-confirm
  renderer; states every pending period and the schedule-computed
  resulting book value (never the stale top-level field); requires
  `book_value_source="finance_books"` and emits a `confirmation_token`.
- `scripts/render_disposal.py` — disposal double-confirm renderer;
  scrap (full write-off) vs. sale (proceeds vs. book value, gain/loss
  stated), requires a reason; requires `book_value_source="finance_books"`
  and emits a `confirmation_token`.
- `scripts/render_movement_draft.py` — transfer draft renderer; cross-
  checks declared source_location against the asset's real current
  location, and flags a stale (>300s old) location snapshot when
  `actual_current_locations_fetched_at` is supplied.
- `scripts/render_report.py` — operational report renderer (schedule
  review, audit checklist); includes `build_schedule_reconciliation()`.
- `scripts/test_erp_client.py`, `scripts/test_render_asset_draft.py`,
  `scripts/test_render_depreciation_run.py`,
  `scripts/test_render_disposal.py`, `scripts/test_render_movement_draft.py`,
  `scripts/test_render_report.py` — unit tests (stdlib `unittest`, no
  network), 47 cases. `health_check()`/`query_resource()`/
  `mutate_resource()` plus the four whitelisted RPC methods were
  additionally verified live against `<erp-instance>` during this build
  (see `references/connector-reference.md` and
  `references/erpnext-assets-docs.md`).

## Extension point

To target a different ERP backend, replace `scripts/erp_client.py`,
`references/connector-reference.md`, and `references/erpnext-assets-
docs.md`. `references/domain-knowledge.md` and this file's instructions
stay untouched — ERP-agnostic in substance.

## Relationships

Consumes no other `qkeee-erp-*` skill directly. Capitalization sources
(Purchase Receipt/Invoice) are conceptually created by
`qkeee-erp-procurement`/`qkeee-erp-accounts-executive` — user-routed,
no direct mechanism. Depreciation Journal Entries and disposal
gain/loss postings land in the same GL `qkeee-erp-mis-analyst` reports
against — no direct handoff, same GL, different lens.
