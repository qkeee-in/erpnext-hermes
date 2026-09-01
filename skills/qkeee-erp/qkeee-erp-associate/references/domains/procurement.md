# Domain: procurement (Supplier, PO, RFQ)

Code lives in `scripts/domains/procurement.py`
(`ALLOWED_WRITE_DOCTYPES = ("Supplier", "Purchase Order", "Request for
Quotation", "Supplier Quotation")`).

This domain has no unique connector logic of its own — the domain logic
below belongs in `render_supplier_draft.py`/`render_po_draft.py`/
`render_report.py`, which don't exist in this skill's scripts/ yet.

## When this domain applies

Onboarding a supplier, creating or checking a Purchase Order, comparing
quotations from an RFQ, reconciling a goods receipt against a PO,
checking a supplier's performance.

## Non-negotiables specific to this domain

- **Never create a live Supplier record with incomplete mandatory KYC/
  bank fields.** This domain's KYC bar is stricter than ERPNext's own
  (confirmed live: ERPNext's hard-mandatory Supplier fields are only
  `supplier_name` + `supplier_type`) — the fuller bar (identity/
  classification, tax ID, bank/payable details) must be enforced before a
  draft is marked "ready." Incomplete extractions must be flagged, never
  silently filled with a placeholder.
- **Draft-only is the hard default for Purchase Order submission absent
  confirmed submission authority — not just "when unsure."** Where no
  Workflow is configured for Purchase Order, role membership (Purchase
  User vs. Purchase Manager/Purchase Master Manager) is the only
  API-visible signal, and it's a heuristic, not a guarantee. Only
  recommend "create-then-submit-on-confirm" when the caller explicitly
  passes confirmed submission authority; otherwise every PO is
  create-as-draft-only, full stop.

## Procedure

1. Follow the activation sequence and `ALLOWED_WRITE_DOCTYPES` above.
2. **Supplier onboarding**: present the drafted, KYC-complete record, get
   explicit confirmation, then `domains.procurement.mutate(...,
   "create")`. Re-fetch via `query_resource` with explicit `fields` (none
   of the checked fields live in a child table) and confirm
   `supplier_group`, `country`, `default_currency`, and any bank/payable
   Link fields resolve to real records. Supplier isn't submittable — this
   post-save re-fetch is the only checkpoint.
3. **Purchase Order drafting**: check the practical warehouse requirement
   for stock-tracked lines first (not visible in the DocType's `reqd`
   flags). Before treating submission authority as confirmed, check for a
   real Workflow on Purchase Order (`query_resource("Workflow",
   filters=[["document_type","=","Purchase Order"]])`); fall back to
   `core.client.get_user_roles()` only if none exists, and treat that as a
   heuristic the user should corroborate, not a determination made
   silently on their behalf — an empty roles list with a non-empty
   warning is ambiguous (no role vs. a failed lookup), not confirmed "no
   authority." Resolve `stock_items` explicitly (query `Item.is_stock_item`
   for every line) rather than relying on an "assume stock" default.
   **Save-draft-then-review-then-submit:** `create` always lands at
   `docstatus 0` regardless of confirmed authority; before ever
   submitting, re-fetch via `core.client.get_resource()` (the list
   endpoint silently drops the line-items child table) and review every
   Link field (`supplier`, each line's `item_code`, `warehouse`,
   `cost_center`). Never chain create straight into submit even when
   authority is confirmed.
4. **RFQ/Supplier Quotation comparison and GRN matching** need a real
   quotation-coverage check (per supplier: was every invited item quoted,
   name exactly what's missing, state that before ranking on price) and a
   real GRN-match check (walk every PO line, return quantity and
   rejected-quantity discrepancies as separate issues on the same line
   where both apply) — don't hand-aggregate coverage or discrepancies
   inline without those two checks. `not_applicable` is only for reports
   with nothing to tie out (a bare PO status lookup) and needs a stated
   reason.
5. **Supplier Scorecard queries are documentation-grounded, not
   universally live-tested** — say so on first real use against a new
   instance, and treat that first real query as the effective validation.

## Quick reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Supplier onboarding | KYC-complete supplier | Refuses "ready" if any KYC/bank field missing or low-confidence |
| PO creation | Purchase Order drafted/placed | Draft-only unless submission authority is confirmed |
| PO status query | Where a PO stands | `status`, `per_received`, `per_billed` |
| RFQ / Supplier Quotation comparison | Best-value supplier, coverage-checked | Flags incomplete coverage |
| GRN matching | Goods receipt reconciled to PO | Quantity + rejected-quantity flagged separately |
| Supplier scorecard/performance | Reliability visibility | Cites underlying counts, not just the score |

## Relationships

Consumes `domains/doc-extraction.md` for supplier KYC docs. Conceptual
counterpart to `domains/sales.md` on the inbound side. Feeds
`domains/accounts.md` for downstream Purchase Invoice matching.
