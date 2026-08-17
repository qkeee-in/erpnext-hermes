# ERPNext accounting documentation map (accounts-executive scope)

Curated pointers into ERPNext/India Compliance documentation, fetched
2026-08-10 while building this skill, plus findings confirmed live
against `<erp-instance>` the same day. Runtime reference, not just a
build-time note: when unsure how a mechanic behaves, fetch the linked URL
directly (via a harness web-fetch tool, if available) rather than
guessing — this file can drift from the live docs, the URL is the source
of truth.

## Core transactional concepts

| Topic | URL | What's there |
| --- | --- | --- |
| Journal Entry | `https://docs.frappe.io/erpnext/journal-entry` | Debit/credit row mechanics, full Entry Type list, multi-currency, document-reference rules — same page `qkeee-erp-mis-analyst` uses for the domain-agnostic mechanics; this skill additionally *writes* JEs |
| Payment Entry | `https://docs.frappe.io/erpnext/user/manual/en/payment-entry` | Receive/Pay/Internal Transfer types; records customer receipts and supplier payments |
| Advance Payment Entry | `https://docs.frappe.io/erpnext/advance-payment-entry` | Recording an advance vs allocating against an invoice later, Unallocated Amount tracking, Payment Reconciliation for matching |
| Accounts Receivable and Payable overview | `https://docs.frappe.io/erpnext/accounts-receivable-and-payable` | High-level only — thin; see the Accounting Reports page below for actual AR/AP mechanics |
| Accounting Reports (AR/AP aging, Sales/Purchase Register) | `https://docs.frappe.io/erpnext/accounting-reports` | Same index `qkeee-erp-mis-analyst` uses — AR/AP section covers payment-terms-based aging and the revaluation-journal filter for multi-currency |

Advance-vs-against-invoice allocation mechanics are documented from
source only, not confirmed via a live partial-allocation round trip on
an ERPNext instance. Treat partial-allocation behavior (Unallocated
Amount tracking, Payment Reconciliation matching) as unverified until
exercised end-to-end; don't present it as field-tested the way the JE
create/submit/cancel path is.

## 3-way match (PO → GRN → Invoice)

No single dedicated doc page found for this ERPNext-specific mechanic; grounded instead in live field introspection against `<erp-instance>`
(2026-08-10):

- **Purchase Order** carries `per_received` and `per_billed` (percent of ordered qty received / percent of ordered value billed) — the concrete fields that tell you whether a PO is fully, partially, or not yet matched.
- **Purchase Receipt** carries `per_billed` (percent of received qty billed).
- Each line item on Purchase Receipt/Purchase Invoice references its originating PO row, which is what lets a match report walk PO → GRN → Invoice for the same item line rather than just comparing document totals.
- Confirmed live: `status` values seen in real data include `"To Bill"`, `"To Receive and Bill"`, `"Completed"` — these are ERPNext's own match-state signals, worth surfacing directly rather than re-deriving from `per_received`/`per_billed` alone.

## Tax Withholding (TDS) — core ERPNext, not India-specific

**Confirmed live 2026-08-10: `apply_tds` and `tax_withholding_category`
are native fields on Purchase Invoice**, and a real `Tax Withholding
Category` record ("TDS - 194J - Professional Services") exists on
`<erp-instance>` with no India Compliance app installed. **TDS/withholding
tax is core ERPNext functionality (the generic "Tax Withholding Category"
mechanism), not gated behind India Compliance** — this corrects an
assumption the module plan's original phrasing ("GST/TDS/e-invoicing/
e-way bill... India-specific ERPNext localization features") could
suggest. Only GST-specific mechanics (GSTIN fields, GSTR returns,
e-invoicing/e-way-bill) actually require India Compliance; TDS via Tax
Withholding Category works on stock ERPNext for any org configuring
withholding categories relevant to their jurisdiction.

## GST / e-invoicing / e-way bill — needs the India Compliance app

**Confirmed live 2026-08-10: `<erp-instance>` has no India Compliance
module installed** (`Module Def` query returned only stock
`frappe`/`erpnext`/`hrms`/`crm`) and Supplier's `tax_id` is a generic
Data field, not a validated GSTIN field. GST/e-invoicing/e-way-bill
capabilities below are documented from source but **could not be
live-validated in this build** — confirm against an India-Compliance-
enabled instance before treating them as field-tested.

`docs.frappe.io`'s own regional India pages (`/erpnext/v12/.../
setup-e-invoicing`, `/erpnext/v14/.../generating_e_invoice`) are
version-pinned and describe an older, now-superseded integration path.
**The current authoritative source is the dedicated India Compliance
app's own docs, `docs.indiacompliance.app`**, not `docs.frappe.io`:

| Topic | URL |
| --- | --- |
| India Compliance overview | `https://docs.indiacompliance.app/docs/getting-started/introduction` |
| GSTIN validation / party autofill | `https://docs.indiacompliance.app/docs/miscellaneous/gstin_verification` |
| e-Invoice generation | `https://docs.indiacompliance.app/docs/ewaybill-and-einvoice/generating_e_invoice` |
| e-Way Bill generation | `https://docs.indiacompliance.app/docs/ewaybill-and-einvoice/generating_e_waybill` |
| TDS configuration (India-specific rate/section setup, distinct from the core Tax Withholding Category mechanism above) | `https://docs.indiacompliance.app/docs/configuration/tds_configuration` |
| GST reports (GSTR-1, GSTR-3B, GSTR-2A/2B reconciliation) | `https://docs.indiacompliance.app/docs/gst-reports/` |

India Compliance's own summary: "Autofill Party and Address details by
entering their GSTIN," "Automated GST e-Invoice generation and
cancellation," "End-to-end GST e-Waybill management," "Advanced purchase
reconciliation based on GSTR-2B and GSTR-2A" for Input Tax Credit claims.

**Government portals remain the ground-truth authority for the
regulation itself** (rates, formats, filing deadlines), per the module
plan — `gst.gov.in`, `einvoice1.gst.gov.in`, `ewaybillgst.gov.in`, and the
Income Tax e-filing portal for TDS rules. India Compliance implements
against these; it is not itself the regulation.

## Reports (map capability → exact `report_name` for `run_query_report()`)

| Capability | `report_name` |
| --- | --- |
| AP/AR aging | `"Accounts Receivable"` / `"Accounts Payable"` |
| Sales/Purchase tax register | `"Sales Register"` / `"Purchase Register"` |
| GST returns (needs India Compliance) | `"GSTR-1"`, `"GSTR-2"` — confirm exact names against a live India-Compliance-enabled instance; not confirmed here |
| GSTR-3B specifically | **No `report_name` identified at all** — unlike GSTR-1/GSTR-2 above (unconfirmed but named), no candidate report name for GSTR-3B was found even in `docs.indiacompliance.app`. The "GST return prep assist (GSTR-1/3B)" capability in `SKILL.md` should be read as GSTR-1 having an unconfirmed-but-named path and **GSTR-3B having no identified path at all** — a materially bigger gap than "unconfirmed," and previously not called out as distinct from the GSTR-1/2 gap. Resolve by fetching `docs.indiacompliance.app/docs/gst-reports/` directly at runtime before promising this half of the capability. |

## Staleness note

Fetched/verified 2026-08-10. Doctype field lists and report filter
schemas should be reconfirmed against the target org's instance directly
(`GET /api/resource/DocType/<DocType Name>`) rather than assumed from
this file — this instance's specific configuration (e.g. which Tax
Withholding Categories exist) will differ per org.
