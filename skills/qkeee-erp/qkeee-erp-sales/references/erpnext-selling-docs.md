# ERPNext Selling module — doc map & live field-schema grounding

Curated pointers into `docs.frappe.io/erpnext` for the Selling module,
plus what this build confirmed live against `<erp-instance>` (2026-08-10)
where docs alone were thin or ambiguous. Consult the linked docs page
directly (fetch it, if a harness web-fetch tool is available) whenever a
mechanic is uncertain at runtime; treat this file's live-confirmed notes
as ground truth over the docs where they conflict, since ERPNext version
drift is real.

## Customer

- Docs: `docs.frappe.io/erpnext/customers`
- Schema-mandatory (live-confirmed): `customer_name`, `customer_type`
  (Select: Company/Individual/Partnership) only.
- **`mobile_no` / `email_id` are `fetch_from: customer_primary_contact.*`
  — not directly writable.** Populating them requires linking a Contact
  via `customer_primary_contact`, see Contact section below.
- Not submittable — `docstatus` stays `0` permanently, same shape as
  Supplier in `qkeee-erp-procurement`. No draft/submit distinction;
  "disable" (`disabled: 1`) is the practical undo, not delete, once a
  Customer has any linked record (confirmed live: delete on a Customer
  with a linked Contact returns `LinkExistsError` with the message "You
  can disable this Customer instead of deleting it.").
- `customer_group` (Link → Customer Group) and `territory` (Link →
  Territory) are not schema-mandatory but are the two fields every
  segment/region sales report is built on — see domain-knowledge.md's
  rationale for why this skill requires them anyway.

## Contact (linked to Customer)

- Docs: `docs.frappe.io/erpnext/contacts`
- Schema-mandatory: none of `first_name`/`email_ids`/`phone_nos`/`links`
  are `reqd` in the schema, but a Contact with no `links` row is an
  orphan (never surfaced against any Customer) and is functionally
  useless for this skill's purpose.
- **Autoname pattern, live-confirmed:** `"<first_name>-<link_name>"`
  where `link_name` is the linked record's name (e.g. a Contact named
  "Qkeee Sales Test Contact" linked to Customer "qkeee-sales-test
  Customer" autonamed to `"Qkeee Sales Test Contact-qkeee-sales-test
  Customer"`). The calling skill needs the Customer's real (possibly
  naming-series-suffixed) name before it can predict the Contact's name
  with certainty.
- `links` is a Dynamic Link child table: `[{"link_doctype": "Customer",
  "link_name": <customer name>}]`. Creating a Contact with this link
  does **not** automatically set `Customer.customer_primary_contact` —
  that's a separate, explicit `Customer` update (see connector-
  reference.md's live validation record, step 6).

## Quotation

- Docs: `docs.frappe.io/erpnext/quotations`
- Schema-mandatory (live-confirmed): `quotation_to` (Link → DocType,
  default `"Customer"`), `transaction_date`, `order_type`, `company`,
  `currency`, `conversion_rate`, `selling_price_list`,
  `price_list_currency`, `plc_conversion_rate`, `items` (table), plus
  `status` (system-managed).
- **`party_name` (Dynamic Link, the actual customer/lead reference) is
  NOT `reqd`** despite being the field that makes the document mean
  anything — see connector-reference.md's live validation record #1.
  This skill requires it.
- Submittable. `status` starts `"Draft"` (`docstatus` 0), goes
  `"Open"` on submit (`docstatus` 1), `"Cancelled"` on cancel
  (`docstatus` 2). Other status values (`Replied`, `Partially Ordered`,
  `Ordered`, `Lost`, `Expired`) are set by downstream actions (a linked
  Sales Order being created, or an explicit "declare lost" action) —
  not something this skill's capabilities drive directly.
- `Quotation Item.item_code` is NOT `reqd` — an `item_name`-only line is
  schema-valid but skips ERPNext's own `is_sales_item` check and prices
  at 0 unless a `rate` is explicitly set. This skill requires
  `item_code`.
- **`Item.is_sales_item` gate, live-confirmed:** a line referencing an
  item with `is_sales_item: 0` fails the whole Quotation create with
  `ValidationError: Following item <item> is not marked as sales item.`
  Resolve `Item.is_sales_item` for every line's `item_code` before
  drafting (mirrors `qkeee-erp-procurement`'s `Item.is_stock_item`
  resolution pattern for Purchase Order lines).
- `warehouse` on each line auto-defaults from the Item master's default
  warehouse when omitted — no equivalent of Purchase Order's hidden
  mandatory-warehouse trap was found here.

## Sales Order

- Docs: `docs.frappe.io/erpnext/sales-order`
- Schema-mandatory (live-confirmed): `customer`, `order_type`,
  `transaction_date`, `company`, `currency`, `conversion_rate`,
  `selling_price_list`, `price_list_currency`, `plc_conversion_rate`,
  `items`, `status`.
- Submittable. On submit, `status` moves from `"Draft"` to a compound
  status reflecting both fulfilment axes (e.g. `"To Deliver and
  Bill"`), and `delivery_status`/`per_delivered`/`billing_status`/
  `per_billed` become the fields to read for "where does this order
  stand" — always report both axes, not a single collapsed status (see
  domain-knowledge.md).
- **Not a creation capability in this skill's scope** (see module
  plan / SKILL.md capability table) — Sales Order is queried for status
  only here; drafting/placing a Sales Order is out of scope, even
  though this build live-tested create/submit to validate the
  connector's query-path field semantics.

## Delivery Note

- Docs: `docs.frappe.io/erpnext/delivery-note`
- Schema-mandatory (live-confirmed): `customer`, `posting_date`,
  `posting_time`, `company`, `currency`, `conversion_rate`,
  `selling_price_list`, `price_list_currency`, `plc_conversion_rate`,
  `items`, `status`.
- **Linking a DN line back to its originating Sales Order line needs
  both `against_sales_order` and `so_detail`** (the specific `Sales
  Order Item` row name) — live-confirmed this is what makes the SO's
  own `per_delivered`/`delivery_status` update correctly on DN submit.
  `against_sales_order` alone was not independently tested; treat a
  queried DN line missing `so_detail` as a likely explanation for a
  fulfilment-tracking mismatch a user reports.
- Submittable. `status` observed: `"Draft"` → (on submit, against a
  fully-invoiced-or-not SO) `"To Bill"` / `"Completed"` depending on
  billing state.
- Query-only capability in this skill's scope (tracking, not creation).

## Built-in reports (module: Selling)

Confirmed present on `<erp-instance>` via `Report` doctype query
(`filters=[["module","=","Selling"]]`): Address And Contacts, Available
Stock for Packing Items, Customer Acquisition and Loyalty, Customer
Credit Balance, Customer-wise Item Price, Customers Without Any Sales
Transactions, Inactive Customers, Item-wise Sales History, Lost
Quotations, Payment Terms Status for Sales Order, Pending SO Items For
Purchase Request, Quotation Trends, Sales Analytics, **Sales Order
Analysis**, Sales Order Trends, Sales Partner Commission Summary, Sales
Partner Target Variance based on Item Group, Sales Partner Transaction
Summary, Sales Person Commission Summary, Sales Person Target Variance
Based On Item Group, Sales Person-wise Transaction Summary, Territory
Target Variance Based On Item Group, Territory-wise Sales.

**Sales Order Analysis** (Script Report) — live-confirmed callable via
`frappe.desk.query_report.run` with filters `{"company": ...,
"based_on": "Sales Order Date", "from_date": ..., "to_date": ...,
"doctype": "Sales Order"}`; returns per-line `delay_days`, `pending_qty`,
`pending_amount`, `billed_amount`, `status` off real transactional data
already present on the instance. Used by `scripts/render_report.py`'s
`build_pipeline()` for the Sales Order side of the pipeline-lite report.

**Quotation Trends** — present but **not live-tested this build**. If a
future session validates it, prefer it over
`build_pipeline()`'s hand-aggregated Quotation-stage counting.

## Roles present (not currently used by this skill)

`Sales User`, `Sales Manager`, `Sales Master Manager` exist as roles on
this instance; no `Workflow` doctype was found configured for either
Quotation or Sales Order (`GET /api/resource/Workflow?filters=
[["document_type","in",["Quotation","Sales Order"]]]` returned empty).
Unlike `qkeee-erp-procurement`'s PO-submission-authority heuristic, this
skill doesn't need a role-based authority signal — Quotation submission
is always a separate, explicitly-confirmed step regardless of who's
asking (see the non-negotiable in `SKILL.md`), and Sales Order/Delivery
Note creation isn't a capability this skill offers at all.
