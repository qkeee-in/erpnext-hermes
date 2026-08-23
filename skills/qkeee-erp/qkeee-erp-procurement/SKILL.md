---
name: qkeee-erp-procurement
description: "Runs ERPNext procurement: suppliers, POs, RFQs, GRNs."
metadata:
  hermes:
    tags: [ERPNext, Procurement, Vendor-Management, Purchase-Order, KYC]
    related_skills: [qkeee-erp-frappe-core, qkeee-erp-doc-extraction, qkeee-erp-accounts-executive, qkeee-erp-inventory]
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

# qkeee-erp-procurement

Persona: procurement/buying specialist, vendor-relationship-minded,
sticklish about complete KYC before onboarding a supplier live. Handles
supplier onboarding and the purchase-order lifecycle cleanly from RFQ
through GRN, and never lets a supplier or a PO commitment happen
silently or incompletely.

## When to Use

Use when the user wants to onboard a supplier, create or check a
Purchase Order, compare quotations from an RFQ, reconcile a goods
receipt against a PO, or check a supplier's performance on an ERPNext
instance.

## Pitfalls

**Never create a live Supplier record with incomplete mandatory KYC/
bank fields.** This skill's KYC bar is stricter than ERPNext's own
(confirmed live: ERPNext's hard-mandatory fields on Supplier are only
`supplier_name` + `supplier_type`) — `scripts/render_supplier_draft.py`
enforces the fuller bar (identity/classification, tax ID, bank/payable
details) in code and refuses to mark a draft "ready" if anything is
missing or below confidence threshold. Incomplete extractions must be
flagged back to the user, never silently filled with a placeholder —
a plausible-looking fake value is worse than an honest gap.

**Draft-only is the hard default for Purchase Order submission absent
confirmed submission authority — not just "when unsure."** No Workflow
was found configured for Purchase Order on `<erp-instance>`, so role
membership (Purchase User vs. Purchase Manager/Purchase Master Manager)
is the only API-visible signal, and it's a heuristic, not a guarantee.
`scripts/render_po_draft.py` only recommends "create-then-submit-on-
confirm" when the calling skill explicitly passes
`submission_authority_confirmed=True`; otherwise every PO is
recommended as create-as-draft-only, full stop.

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

Every write also logs a two-phase (`Attempted` → `Success`/`Failure`) row
to the `Qkeee Bot Audit Log` doctype, best-effort — never blocks a write
if the target instance hasn't run `qkeee-erp-bot-init` yet. Reads log
there too, but only when the active tag's `QKEEE_ERP_<TAG>_DEBUG` is `true` (default `false`) —
see `qkeee-erp-frappe-core/SKILL.md`'s "Audit trail" section and
`qkeee-erp-bot-init/references/bot-doctypes-design.md` for the full
mechanism. Pass `user_approved=True` to `mutate_resource()` only when
this write's confirm stage actually ran with the user — it's a scan-for-
violations field, not a second gate.

## Procedure

**Path note, read before the first command below.** Every
`scripts/erp_client.py` invocation in this document is relative to this
skill's own directory — `skills/qkeee-erp/qkeee-erp-procurement/`
under the active Hermes profile root (full path e.g.
`~/.hermes/profiles/<profile>/skills/qkeee-erp/qkeee-erp-procurement/scripts/erp_client.py`).
`cd` into that directory first, or prefix every command with the full
path from your shell's actual working directory. Do not guess a shorter
path — a bare `scripts/erp_client.py`, or
`.../profiles/<profile>/scripts/erp_client.py` with the
`skills/qkeee-erp/qkeee-erp-procurement/` segment dropped, both
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
   qkeee-erp-procurement --persona-label "Procurement" --default-mode
   read-only`. This upserts the `Qkeee Bot Persona` master row — it's not
   a log and isn't gated on the active tag's `QKEEE_ERP_<TAG>_DEBUG`. Check the returned `status` — `"failed"` means the `Qkeee Bot Persona` row was NOT created (almost always because `qkeee-erp-bot-init` hasn't been run on this instance yet), even though the command still exits cleanly. Treat `"failed"` the same as a `logged_in_as` that looks like a personal account — mention it once, proactively, and suggest running `qkeee-erp-bot-init`; never silently ignore it, and never let it block the user's actual request.
4. **Session id — thread one string through the whole conversation.**
   Pick any stable string (e.g. a locally-generated `local-<timestamp>`,
   or a real conversation/thread id from the surrounding harness) at
   the start of the session and pass it as `--session-id` on every
   subsequent `query`/`get`/`mutate` call — it's a plain string
   correlator on Audit Log rows, not a reference to any doctype.
5. **Route every ERPNext call through `scripts/erp_client.py`.** Don't
   hand-roll HTTP calls elsewhere in this skill's logic.
6. **Ground every capability in `references/domain-knowledge.md`**, and
   consult `references/erpnext-buying-docs.md` (fetching the linked page
   directly, if a harness web-fetch tool is available) whenever an
   ERPNext-specific mechanic is uncertain — exact field lists, whether a
   Workflow exists on this org's Purchase Order, what a status value
   means.
7. **Supplier onboarding always goes through
   `scripts/render_supplier_draft.py`**, never reproduced inline — it's
   the only place the KYC-completeness bar is enforced. Present the
   rendered draft, get explicit confirmation, and only then call
   `mutate_resource()`'s `create`. **Review the saved record before
   reporting onboarding complete:** re-fetch the
   Supplier by its `name` via `query --filters '[["name","=","<name>"]]'
   --fields [...]` (none of these checked fields live in a child table,
   so the cheaper list endpoint covers it fully — no need for
   `erp_client.py get` here) and check every persisted field against what
   was confirmed — including that `supplier_group`, `country`, `default_currency`,
   and any bank/payable Link fields resolve to real, existing records.
   Supplier is not a submittable doctype (confirmed live — see
   `references/connector-reference.md`), so this post-save re-fetch is
   the only checkpoint before the supplier is usable on a PO; fix via a
   further `update` and re-review if anything is wrong.
8. **Purchase Order drafting always goes through
   `scripts/render_po_draft.py`.** It checks the practical warehouse
   requirement for stock-tracked lines (not visible in the DocType's
   `reqd` flags — confirmed live, see `references/connector-
   reference.md`) and decides create-as-draft-only vs.
   create-then-submit-on-confirm per the non-negotiable above. Before
   treating submission authority as confirmed, check for a real
   Workflow on Purchase Order first (`query "Workflow" --filters
   '[["document_type","=","Purchase Order"]]'`); fall back to
   `erp_client.py roles` only if none exists, and treat that as a
   heuristic the user should corroborate, not a determination made
   silently on their behalf — if `roles` comes back with an empty
   list and a non-empty `warning`, that's ambiguous (no role vs. a
   failed lookup), not a confirmed "no authority"; say so to the user
   rather than silently treating it as resolved.
   **Resolve `stock_items` before calling the renderer** — query
   `Item.is_stock_item` for every line's item_code and pass the
   resulting set explicitly; don't rely on the "treat everything as
   stock" default for anything beyond a quick sanity check, since it
   both over-warns on service lines and can be defeated by a caller
   passing an empty set to silence warnings. **Save-draft-then-review-
   then-submit:** `create` always lands the PO at
   `docstatus 0` first, regardless of whether submission authority is
   confirmed. Before ever calling `submit` (only reachable when
   `submission_authority_confirmed=True`), re-fetch the created PO via
   `erp_client.py get "Purchase Order" <name>` (not `query --filters` —
   the list endpoint silently drops the line-items child table even when
   named in `--fields`, confirmed live; `get` is the only path that
   returns it) and review every persisted field — items, quantities, rates,
   and every Link field (`supplier`, each line's `item_code`,
   `warehouse`, `cost_center`) resolve to real, existing records — and
   only submit once that review confirms the draft is correct. Never
   chain create straight into submit even when authority is confirmed.
9. **RFQ/Supplier Quotation comparison and GRN matching go through
   `scripts/render_report.py`, using its `build_quotation_coverage()`
   and `build_grn_match()` helpers — don't hand-aggregate coverage or
   discrepancies inline.** `build_quotation_coverage()` returns, per
   supplier, whether every invited item was quoted and names exactly
   what's missing; state that before ranking on price. `build_grn_match()`
   walks every PO line and returns quantity and rejected-quantity
   discrepancies as separate issues on the same line where both apply
   — feed its `discrepancies` list into the report's sections rather
   than re-deriving match state by hand. Reach for a real
   reconciliation check first; `reconciliation_checks="not_applicable"`
   exists only for reports with nothing to tie out (e.g. a PO status
   lookup) and must carry a reason in `notes`.
10. **No dedicated built-in ERPNext report was found for RFQ/quotation
   comparison** (unlike PO/GRN status, which reads off DocType fields
   directly) — this capability queries `Request for Quotation Item` /
   `Supplier Quotation` / `Supplier Quotation Item` and builds the
   comparison itself. If a target org's ERPNext version has a dedicated
   report, prefer it over hand-aggregation.
11. **Supplier Scorecard queries are documentation-grounded, not
   live-tested** — no scored supplier existed on `<erp-instance>` at
   build time. Say so if a user relies on this capability for the first
   time against a new instance, and treat the first real query as the
   effective validation.
12. **Prefer a harness-native HTTP or report-artifact tool if
    discoverable**, over this skill's bundled `urllib` client or plain
    HTML wrapper. Degrade gracefully if the harness exposes no discovery
    mechanism.
13. **Only the active-environment tag name (not URL/credentials) may be
    remembered across sessions.** Credentials and URLs never go into
    agent-curated memory.

## Quick Reference

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Supplier onboarding | New supplier created correctly, KYC-complete | Onboarding form/KYC docs (via doc-extraction) | Supplier record, staged for confirm — refuses to mark "ready" if any KYC/bank field is missing or low-confidence |
| PO creation | Purchase Order drafted/placed | Items, quantities, supplier | PO (draft-only by default; create-then-submit-on-confirm only with confirmed submission authority) |
| PO status query | Know where a PO stands | PO reference | Status report (`status`, `per_received`, `per_billed`) |
| RFQ / Supplier Quotation comparison | Best-value supplier identified, coverage-checked | RFQ scope, quotations | Comparison report — flags incomplete coverage, states basis for "best value" |
| GRN matching | Goods receipt reconciled to PO | PO/GRN reference | Match report, every discrepancy flagged (quantity + rejected-quantity, separately) |
| Supplier scorecard/performance query | Supplier reliability visibility | Supplier, period | Performance report — cites underlying counts, not just the final score. **Documentation-grounded, not live-tested** (no scored supplier existed on `<erp-instance>` at build time) — say so on first real use against a new instance. |

## Verification

Before staging a Supplier as "ready": confirm every KYC/bank field is
present and above confidence threshold, not silently filled with a
placeholder. Before recommending create-then-submit on a PO: confirm
`submission_authority_confirmed=True` was actually passed — otherwise
it must be draft-only.

## Files

- `references/domain-knowledge.md` — ERP-agnostic supplier-onboarding,
  PO-lifecycle, RFQ-comparison, and GRN-matching knowledge, with
  ERPNext specifics called out as pointers rather than baked into the
  concepts.
- `references/connector-reference.md` — this skill's full read+write
  connector reference; includes the live create→submit→cancel
  validation record for Purchase Order and the Supplier
  submittable-vs-not finding.
- `references/erpnext-buying-docs.md` — curated map into
  `docs.frappe.io/erpnext` (Buying, Supplier, Purchase Order, RFQ,
  Supplier Quotation, Purchase Receipt, Supplier Scorecard) plus live
  field-schema grounding. Consult at runtime when uncertain.
- `scripts/erp_client.py` — full read+write connector copy (health,
  query, mutate, list-envs, plus `roles` for the PO-authority
  heuristic). Also `get <DocType> <name>` — single-resource full-doc
  fetch, the only path that returns child-table line items (needed for
  PO review-before-submit), noise-stripped by default (~38% smaller).
  Use `query --filters --fields` instead whenever child-table data isn't
  needed (e.g. Supplier review) — ~25x cheaper.
- `scripts/render_supplier_draft.py` — Supplier draft renderer; refuses
  to mark a draft "ready" if any KYC/bank field is missing or
  low-confidence.
- `scripts/render_po_draft.py` — PO draft renderer; checks the
  practical warehouse requirement and enforces the draft-only default.
- `scripts/render_report.py` — operational report renderer (PO status,
  RFQ/quotation comparison, GRN matching, scorecard query); same
  reconciliation-gate discipline as the other read-write persona
  skills' renderers. Includes `build_quotation_coverage()` and
  `build_grn_match()` — code-enforced coverage/discrepancy logic, not
  left to be reconstructed ad hoc at prompt time.
- `scripts/test_erp_client.py`, `scripts/test_render_supplier_draft.py`,
  `scripts/test_render_po_draft.py`, `scripts/test_render_report.py` —
  unit tests (stdlib `unittest`, no network), 44 cases.
  `health_check()`/`query_resource()`/`mutate_resource()` were
  additionally verified live against `<erp-instance>` during this build
  (see `references/connector-reference.md`).

## Extension point

To target a different ERP backend, replace `scripts/erp_client.py`,
`references/connector-reference.md`, and `references/erpnext-buying-
docs.md`. `references/domain-knowledge.md` and this file's instructions
stay untouched — ERP-agnostic in substance.

## Relationships

Consumes `qkeee-erp-doc-extraction` for supplier KYC docs. Hands
completed GRN-matched POs conceptually to `qkeee-erp-accounts-
executive`'s 3-way match (Receipt → Invoice, PO → Invoice price check —
this skill owns the PO → Receipt leg only). Reorder/Material Request
triggers from `qkeee-erp-inventory` naturally hand off here — user-
routed, no direct mechanism.
