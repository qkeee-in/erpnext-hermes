# ERPNext buying/procurement documentation map (procurement scope)

Curated pointers into `docs.frappe.io/erpnext`, plus findings confirmed
live against `<erp-instance>`. Runtime reference, not just a build-time
note: when unsure how a mechanic behaves, fetch the linked URL directly
(via a harness web-fetch tool, if available) rather than guessing — this
file can drift from the live docs, the URL is the source of truth.

## Core transactional concepts

| Topic | URL | What's there |
| --- | --- | --- |
| Buying module overview | `https://docs.frappe.io/erpnext/buying` | High-level procure-to-pay flow: RFQ → Supplier management → PO → Invoice → Stock update. Thin — doesn't link out to every doctype page directly. |
| Supplier | `https://docs.frappe.io/erpnext/supplier` | Supplier groups, tax fields, default bank account, payment terms. Doesn't document formal KYC-attachment workflows — those are this skill's own bar, not ERPNext's. |
| Purchase Order | `https://docs.frappe.io/erpnext/purchase-order` | Draft → Submitted → Hold/Close lifecycle, Material Request/Supplier Quotation linkage, downstream Receipt/Invoice/Payment/JE creation. No explicit approval-workflow documentation — confirmed live this instance has none configured (see below). |
| Request for Quotation (RFQ) | `https://docs.frappe.io/erpnext/request-for-quotation` | Multi-supplier quote solicitation, user-initiated or supplier-portal-initiated quotation submission, status flips to "Received" per supplier. |
| Supplier Quotation | `https://docs.frappe.io/erpnext/supplier-quotation` | Recorded quote from a supplier; PO can be generated directly from an accepted quotation. |
| Purchase Receipt (GRN) | `https://docs.frappe.io/erpnext/purchase-receipt` | Accepted/rejected quantities, separate warehouses for each, `per_billed` for billing reconciliation, "Get Items from Purchase Order" creation path. |
| Supplier Scorecard | `https://docs.frappe.io/erpnext/supplier-scorecard` | Weighted-period scoring (default: linear over last 12 periods), criteria built from variables like total/accepted/rejected item counts and delivery counts. |

## Supplier — field grounding (live)

**ERPNext's own hard-mandatory fields on Supplier are only
`supplier_name` (Data) and `supplier_type` (Select: Company/Individual/
Partnership)** — confirmed via `GET /api/resource/DocType/Supplier`.
Everything else this skill treats as required (`supplier_group`,
`country`, `tax_id`, bank details) is this skill's own stricter KYC bar
— see `references/domain-knowledge.md`. Relevant non-mandatory fields
present on this instance: `default_bank_account` (Link → Bank Account),
`tax_category` (Link → Tax Category), `payment_terms` (Link → Payment
Terms Template), `on_hold` (Check, plus `hold_type`/`prevent_rfqs`/
`prevent_pos` — an org can block RFQs and/or POs against a supplier
without disabling it outright).

**No India Compliance app on `<erp-instance>`** (same finding as
`qkeee-erp-accounts-executive`'s build) — `tax_id` is a generic Data
field, no dedicated GSTIN validation. Confirm app installation before
promising GSTIN-specific supplier validation on a given org's instance.

**Supplier is not a submittable doctype** — confirmed live: a created
Supplier has `docstatus: 0` permanently, no submit step, and (if never
referenced by anything downstream) can be deleted outright via `DELETE
/api/resource/Supplier/<name>` — confirmed live, a never-referenced test
Supplier deleted cleanly with `{"data": "ok"}`. This means the
"draft-only" staging discipline for Supplier onboarding happens entirely
at this skill's layer (Stage/Confirm before the create call), not via
ERPNext's own draft/submit mechanism the way Purchase Order has one.

## Purchase Order — field grounding (live)

Mandatory per `GET /api/resource/DocType/Purchase%20Order`: `title`,
`naming_series`, `supplier`, `transaction_date`, `company`, `currency`,
`conversion_rate`, `items` (child table), `status`. Real `status` values
observed in live data: `Draft`, `To Receive and Bill`, `To Bill`,
`Completed`, plus the schema additionally lists `On Hold`, `To Receive`,
`Cancelled`, `Closed`, `Delivered`. `per_received`/`per_billed` (both
Percent) are the concrete progress fields — a PO at `per_received: 100,
per_billed: 0` is fully received but not yet billed ("To Bill"); one at
`per_received: 0` is still fully open ("To Receive and Bill").

**Practical requirement not visible in `reqd` flags — confirmed live the
hard way:** creating a PO with a stock-tracked item line and no
`warehouse` on that line fails with `ValidationError: Row #1: Warehouse
is mandatory for stock Item <item>` (from
`erpnext.buying.utils.validate_stock_item_warehouse`), even though the
DocType schema doesn't mark `warehouse` as `reqd`. `scripts/
render_po_draft.py` checks this explicitly rather than trusting the
declared schema alone.

**No Workflow doctype configured for Purchase Order on this
instance** — confirmed via `GET /api/resource/Workflow?filters=
[["document_type","=","Purchase Order"]]` returning an empty result.
Procurement-relevant roles present: `Purchase User`, `Purchase Manager`,
`Purchase Master Manager` (`GET /api/resource/Role?filters=
[["name","like","%Purchase%"]]`). Absent a real Workflow, role
membership is the only API-visible signal for submission authority —
`scripts/erp_client.py`'s `get_user_roles()` fetches it, but this is a
heuristic per `references/domain-knowledge.md`, not a guarantee any org
intends role membership alone to authorize every submission.

**Live create → submit → cancel round trip confirmed**
(same temporary-key technique as prior skill builds): created a draft
PO (`PUR-ORD-2026-00007`, supplier `Mauli Tea`, company `Qkeee LLP`, 1
line `Raw Item-1` qty 5 @ rate 10, warehouse `Stores - QL`), confirmed
`docstatus: 0`, `status: "Draft"`, `total: 50.0`; submitted via the
fetch-then-submit two-step path, confirmed `docstatus: 1`, `status: "To
Receive and Bill"`; cancelled, confirmed `docstatus: 2`, `status:
"Cancelled"`. Test record left in place, cancelled, labeled via
`user_remark`. Read-only gate behavior (refuses `create` before any HTTP
call when `mode != "read-write"`) is identical to every other
`qkeee-erp-*` connector copy — see `qkeee-erp-core`'s reference for
that mechanism, not re-tested here since it's shared, unmodified code.

## RFQ / Supplier Quotation / Purchase Receipt — field grounding (live)

Mandatory fields confirmed via `GET /api/resource/DocType/<name>`:

- **Request for Quotation**: `naming_series`, `company`,
  `transaction_date`, `status` (Draft/Submitted/Cancelled — no
  in-between "Sent" state at the schema level; supplier-level status
  lives in the `suppliers` child table instead), `suppliers` (child
  table), `items` (child table), `subject`.
- **Supplier Quotation**: `naming_series`, `supplier`, `company`,
  `status` (Draft/Submitted/Stopped/Cancelled/Expired), `transaction_
  date`, `currency`, `conversion_rate`, `items` (child table).
- **Purchase Receipt**: `naming_series`, `supplier`, `posting_date`,
  `posting_time`, `company`, `currency`, `conversion_rate`, `items`
  (child table), `base_net_total`, `status` (Draft/Partly Billed/To
  Bill/Completed/Return/Return Issued/Cancelled/Closed). Real data
  observed: `MAT-PRE-2026-00001` through `-00005`, statuses `Completed`
  and `To Bill`, `per_billed` 0–100.

**GRN matching field grounding** — `Purchase Receipt Item` carries
`received_qty`, `qty`, `rejected_qty`, `rate`, `amount`, `billed_amt`,
plus `purchase_order` (Link) and `purchase_order_item` (Data, the
originating PO row's ID) — this is what lets a match report walk PO →
Receipt for the same line rather than comparing document totals only.
(`Purchase Invoice Item` carries the equivalent `purchase_order`/
`po_detail`/`purchase_receipt`/`pr_detail` fields for the Receipt →
Invoice leg, which is `qkeee-erp-accounts-executive`'s side of the
3-way match, not this skill's.)

## Supplier Scorecard — not live-validated

No Supplier Scorecard records exist on `<erp-instance>` at build time —
schema confirmed (`period`, `weighting_function`, `standings` and
`criteria` child tables, all mandatory), but a real scored supplier
wasn't available to validate the query/report path end-to-end. Treat
the scorecard query capability as **documentation-grounded, not
live-tested** until an org's instance has real scorecard data to check
against.

## RFQ/Supplier Quotation comparison — no dedicated built-in report found

No single `run_query_report()`-compatible built-in ERPNext report was
identified for "compare quotations against an RFQ" (unlike accounts-
executive's aging/register reports, which map cleanly to named built-in
reports) — this capability is built by querying `Request for Quotation
Item`/`Supplier Quotation`/`Supplier Quotation Item` directly and
constructing the comparison in `scripts/render_report.py`, not by
calling a named report. If a future build finds a dedicated report on a
newer ERPNext version, prefer it over hand-aggregation per the
library-wide "prefer the built-in report" convention.

## Staleness note

Doctype field lists, mandatory flags, and
which roles exist should be reconfirmed against the target org's
instance directly (`GET /api/resource/DocType/<DocType Name>`, `GET
/api/resource/Role`) rather than assumed from this file — this
instance's specific configuration (e.g. whether a Workflow exists on
Purchase Order) will differ per org, and this skill's SKILL.md
instructs checking for one rather than assuming none exists just
because `<erp-instance>` had none.
