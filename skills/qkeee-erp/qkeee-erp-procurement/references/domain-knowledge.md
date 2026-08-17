# qkeee-erp-procurement domain knowledge

ERP-agnostic in substance — this is what a vendor-relationship-minded
procurement/buying specialist knows about supplier onboarding and the
purchase-order lifecycle, independent of which system executes it.
`references/connector-reference.md` and `scripts/erp_client.py` are the
ERPNext-specific layer; ERPNext-specific asides below point at
`references/erpnext-buying-docs.md` rather than being baked into the
concepts themselves.

## Supplier onboarding — KYC completeness before "live"

A supplier record isn't just a name — it's the anchor for every future
PO, invoice, and payment against that vendor, so what's missing at
onboarding time becomes a downstream blocker (a PO that can't be paid
because there's no bank account on file, a compliance gap because tax
registration was never captured). **This skill's KYC bar is
deliberately stricter than ERPNext's own hard-mandatory fields** (which,
confirmed live, are only supplier name + supplier type) — see
`SKILL.md`'s non-negotiable and `scripts/render_supplier_draft.py`,
which enforces this in code:

- **Identity & classification**: legal name, supplier group (for
  spend-category reporting), supplier type (Company/Individual/
  Partnership — changes what documentation is even reasonable to ask
  for), country.
- **Tax registration**: a tax ID field, present and plausible for the
  supplier's country — not just non-empty, since a placeholder value is
  worse than a flagged gap (it looks complete but isn't).
- **Bank/payable details**: without these, the supplier can be created
  but can never actually be paid once a PO/Invoice exists against it —
  treat "no bank details yet" as a decision to surface to the user
  explicitly, not something to silently defer.
- **Never fill a missing field with a plausible-looking placeholder.**
  An extraction-derived field below the confidence threshold is exactly
  as much a gap as a field that's simply empty — flag both the same way.

## Purchase Order lifecycle and submission authority

A PO moves from **Draft** (freely editable) to **Submitted** (locked,
`docstatus 1`, now visible to the supplier and to downstream Receipt/
Invoice creation) — submission is the meaningful gate, not creation
itself. Whether the acting user is *authorized* to submit (vs. only
draft) is an organizational fact this skill cannot always determine
from the API alone:

- Some orgs configure a genuine approval Workflow (multi-step, role-
  gated) on Purchase Order; others rely purely on role permissions
  (e.g. "Purchase User" can create/draft, "Purchase Manager"/"Purchase
  Master Manager" can submit) with no separate Workflow document at
  all — **confirmed live against `<erp-instance>`: no Workflow is
  configured for Purchase Order on that instance**, so role membership
  is the only signal the connector layer can check, and it's a
  heuristic, not a guarantee (a user could hold the Purchase Manager
  role without their org intending that to mean "always free to
  submit any PO").
- **The hard default, absent confirmed authority, is draft-only** — not
  "when unsure" as a soft preference. `scripts/render_po_draft.py`
  encodes this: a draft only recommends "create-then-submit-on-confirm"
  when the calling skill explicitly passes
  `submission_authority_confirmed=True` (from a role check the user
  has corroborated, or the user stating outright that they hold
  submission authority for this PO).
- **A PO needing a warehouse per stock-tracked line item is a practical
  requirement ERPNext enforces at `validate()`, not one visible in the
  field schema's `reqd` flags** — confirmed live: a stock item line
  with no warehouse fails PO creation with a specific ValidationError,
  even though `warehouse` isn't marked mandatory on the DocType
  definition. Always resolve a real warehouse for every stock-tracked
  line before staging a draft as ready — see
  `references/erpnext-buying-docs.md`.

## RFQ → Supplier Quotation comparison

The point of routing a purchase through an RFQ rather than going
straight to a PO is competitive, auditable sourcing — multiple
suppliers quoted the same scope, on record, so the eventual choice can
be justified later. Comparing quotations is not just "which number is
lowest":

- **Coverage first**: did every supplier invited actually quote every
  item, or is the comparison silently partial (a supplier who quoted 3
  of 5 items looks cheaper on those 3 but isn't directly comparable on
  total cost)? Flag incomplete coverage explicitly rather than
  comparing only the items everyone happened to quote.
- **Total landed cost, not line-item rate alone** — freight, taxes,
  payment terms (a lower price with 30-days-from-delivery payment terms
  isn't strictly worse than a higher price requiring advance payment;
  it's a different trade-off the user should see stated, not resolved
  for them).
- **Delivery timeline** matters as much as price for anything with a
  required-by date — a cheaper quote that can't deliver in time isn't
  actually the better option.
- Present the comparison with the basis for "best value" stated
  explicitly, not just a sorted price column — a reviewer needs to see
  *why* one supplier is recommended, not just that it's cheapest.

## GRN matching (PO → Goods Receipt)

Confirms that what was received matches what was ordered before the
Receipt is treated as clean — the procurement-side half of the 3-way
match (`qkeee-erp-accounts-executive` owns the invoice-side half,
Receipt → Invoice and PO → Invoice price checks; this skill's GRN
matching is the PO → Receipt leg specifically):

- **Quantity**: did the received quantity match the ordered quantity,
  or is there a documented partial/over-receipt? ERPNext's
  `per_received` (on PO) and each Purchase Receipt Item's
  `received_qty`/`rejected_qty` make this checkable directly rather
  than re-deriving match state by hand — see
  `references/erpnext-buying-docs.md`.
- **Rejected quantity is not the same fact as "not received"** — an
  item can arrive, get inspected, and be rejected (quality failure);
  that's a supplier-quality signal worth surfacing on its own, not just
  folded into "received less than ordered."
- Report every discrepancy found across all line items, not just the
  first mismatch — a partial-receipt on line 3 doesn't make line 5's
  independent rejection any less worth flagging.

## Supplier scorecard / performance

A scorecard is a rolling, weighted judgment (typically the last several
evaluation periods, most-recent-weighted) built from concrete
transaction facts — items received, accepted vs. rejected counts,
delivery timeliness, quotation responsiveness — not a subjective
rating. When reporting a supplier's standing, cite the underlying
counts the score is built from, not just the final number — a score
alone doesn't tell a buyer *why* a supplier trended down this quarter.
See `references/erpnext-buying-docs.md` for the concrete ERPNext
DocType shape (Supplier Scorecard, Scorecard Criteria, Scorecard
Standing).

## Regional/regulatory scope note

Supplier tax-ID capture at onboarding time is this skill's concern
(does a tax ID exist and look plausible for the declared country); the
downstream compliance mechanics that consume it (GSTIN validation,
GST return filing, TDS withholding on payments to this supplier) are
`qkeee-erp-accounts-executive`'s domain, not duplicated here — this
skill hands off a supplier record with the field populated, it doesn't
validate the regulatory correctness of the value itself.
