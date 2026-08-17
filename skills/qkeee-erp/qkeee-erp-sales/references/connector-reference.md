# qkeee-erp-sales connector reference

This skill's copy of the `qkeee-erp` connector layer, synced from the
canonical version in `qkeee-erp-core/references/connector-reference.md`.
Carries the full read+write path (`mutate_resource`), plus
`run_query_report()` (shared with `qkeee-erp-accounts-executive`'s
pattern) for the built-in "Sales Order Analysis" report. Write-capable
for exactly two DocTypes: Customer and Quotation (+ Contact, as part of
Customer onboarding's linked-contact leg) — Sales Order and Delivery
Note are query-only in this skill's scope.

## Auth

ERPNext (Frappe framework) REST API, token auth:

```
Authorization: token <api_key>:<api_secret>
```

Keys are generated per ERPNext user via **User → API Access → Generate
Keys** — an org-side onboarding step, not automated here.

**Must be a dedicated bot/integration user, not a human's login.** All
`qkeee-erp-*` skills share one ERPNext identity for reads/writes.
Generate this key against a dedicated integration/bot user (e.g.
`qkeee-erp-bot@<org>`) — never against an individual staff member's
personal account, or every write in ERPNext attributes to that person
regardless of who actually asked in chat. See `qkeee-erp-core`'s
reference for the full rationale.

## Environment / tag model

Same tagged model as every `qkeee-erp-*` skill — see `qkeee-erp-core`'s
reference for the full table. At install, only
`QKEEE_ERP_DEFAULT_BASE_URL`/`_API_KEY`/`_API_SECRET` are prompted for
(tag `DEFAULT`); adding a second/third environment is a runtime action.

## Endpoints used

| Purpose | Method | Path |
| --- | --- | --- |
| Health check | GET | `/api/method/frappe.auth.get_logged_user` |
| Query a DocType | GET | `/api/resource/<DocType>?filters=...&fields=...&limit_page_length=...` |
| Run a built-in report | GET | `/api/method/frappe.desk.query_report.run?report_name=...&filters=...` |
| Create | POST | `/api/resource/<DocType>` |
| Update | PUT | `/api/resource/<DocType>/<name>` |
| Submit (step 1) | GET | `/api/resource/<DocType>/<name>` |
| Submit (step 2) | POST | `/api/method/frappe.client.submit` |
| Cancel | POST | `/api/method/frappe.client.cancel` |
| Delete | DELETE | `/api/resource/<DocType>/<name>` |
| Best-effort audit comment | POST | `/api/method/frappe.desk.form.utils.add_comment` — body `{"reference_doctype": "...", "reference_name": "...", "content": "..."}` |

**Health check confirms connectivity + auth only**, not query/write-time
permission on a specific DocType — report a later 403/PermissionError as
its own distinct failure mode.

**Submit is two calls, not one** — see `mutate_resource()`'s docstring.
This also means submit reposts every stored field verbatim, including
any sensitive fields already on the record — expected, not a scope leak.

**Response shape differs by action.** `create`/`update`/the GET before
submit return `{"data": {...doc...}}`. `submit` and `cancel`
(whitelisted RPC methods, not REST resource calls) return `{"message":
{...doc...}}` instead — reconfirmed live during this skill's own
Quotation/Sales Order/Delivery Note round trips, consistent with the
finding first made during `qkeee-erp-accounts-executive`'s build.

## Live validation record (<erp-instance>)

Temporary API key/secret generated via session login +
`frappe.core.doctype.user.user.generate_keys`, per admin credentials the
user provided fresh this session (not stored — see
`qkeee-erp-demo-instance` memory). **Note for future builds against this
instance:** `generate_keys` appears to invalidate a previously-issued
secret on repeat calls (or the secret degrades quickly) — mint once,
verify immediately, and don't call `generate_keys` again mid-session;
two of this build's mint attempts produced credentials that 401'd within
seconds of being issued, the third (used for the rest of the build)
worked throughout.

1. **Quotation, no `party_name`:** created successfully
   (`SAL-QTN-2026-00001`) with `quotation_to: "Customer"` and no
   `party_name` at all, and with an `item_name`-only line (no
   `item_code`) accepted at `rate: 0` unless a rate was explicitly set.
   ERPNext's own schema does not flag `party_name` or `Quotation
   Item.item_code` as mandatory — both are practically required for a
   real business document, encoded as this skill's own stricter bar in
   `scripts/render_quotation_draft.py`. Deleted cleanly (`docstatus`
   still 0, never submitted).
2. **Quotation with `item_code` set to a non-sales-enabled item**
   (`Raw Item-1`, `is_sales_item: 0`) failed live: `ValidationError:
   Following item Raw Item-1 is not marked as sales item.` Retried
   against `SKU-1` (`is_sales_item: 1`, `is_stock_item: 1`) — succeeded;
   `warehouse` on the line auto-defaulted to the Item's default
   warehouse (`Finished Goods - EI`) with no manual override needed —
   unlike Purchase Order's live-discovered hidden warehouse requirement
   in `qkeee-erp-procurement`, there is no equivalent trap here.
3. **Quotation create → submit → cancel round trip**
   (`SAL-QTN-2026-00001`, party `qkeee-sales-test Customer`, company
   `Enfasco Inc.`, 1 line `SKU-1` qty 3 @ rate 15): `docstatus` 0
   (`status: "Draft"`) → submit → `docstatus` 1 (`status: "Open"`) →
   cancel → `docstatus` 2 (`status: "Cancelled"`). Confirms `status`
   transitions Draft → Open on submit, matching this skill's
   non-negotiable that submit (not create) is the real "formal
   commitment" step.
4. **Sales Order create → submit round trip** (`SAL-ORD-2026-00005`,
   customer `qkeee-sales-test Customer`, company `Enfasco Inc.`, 1 line
   `SKU-1` qty 3 @ rate 15): create → `docstatus` 0, `status: "Draft"`.
   Submit → `docstatus` 1, `status: "To Deliver and Bill"`,
   `delivery_status: "Not Delivered"`, `per_delivered: 0`,
   `billing_status: "Not Billed"`, `per_billed: 0`. Same
   schema-vs-practical gap pattern as Quotation Item was NOT found here
   — `warehouse` auto-defaults the same way.
5. **Delivery Note create → submit against the Sales Order above**
   (`MAT-DN-2026-00002`): the DN line was built with
   `against_sales_order: "SAL-ORD-2026-00005"` **and**
   `so_detail: "568fi4rtrs"` (the specific Sales Order Item row name,
   read off the SO's own `items[0].name`). After DN submit, re-querying
   the Sales Order showed `status: "To Bill"`, `delivery_status: "Fully
   Delivered"`, `per_delivered: 100.0` — confirming the SO-side
   fulfilment tracking updates correctly **when `so_detail` is
   supplied**. This build did not independently test omitting
   `so_detail` (only `against_sales_order`) to see whether per-line
   tracking degrades — flagged as a gap below; `scripts/
   render_report.py`'s DN tracking report calls out missing `so_detail`
   linkage as a likely explanation if a caller reports a fulfilment
   mismatch.
6. **Customer create, schema-minimal** (`customer_name` +
   `customer_type` only): succeeded immediately — confirms ERPNext's own
   bar is looser than this skill's KYC bar, same pattern as
   `qkeee-erp-procurement`'s Supplier finding. `Customer.mobile_no`/
   `Customer.email_id` are `fetch_from: customer_primary_contact.*` —
   **not directly settable on the Customer record**, and NOT
   auto-populated just because a Contact's own `links` table points at
   the Customer. Confirmed the full 3-step sequence live: (1) create
   Contact with `links: [{"link_doctype": "Customer", "link_name":
   "qkeee-sales-test Customer"}]` — autonamed
   `"<first_name>-<link_name>"`; (2) `Customer.customer_primary_contact`
   stayed `null` after step 1 alone; (3) explicit `mutate Customer
   update` with `customer_primary_contact` set to the Contact's name —
   only after this did `Customer.mobile_no`/`email_id` populate.
   `scripts/render_customer_draft.py` stages all three payloads in this
   order.
7. **Delete/LinkExistsError, reconfirmed for this skill's doctypes:** a
   never-referenced test Quotation (SAL-QTN-2026-00001, never submitted)
   and a schema-minimal test Customer (before it had a linked Contact)
   both deleted cleanly. Once cancelled-but-linked (SO/DN with real
   downstream references, or a Customer with a linked Contact), delete
   fails with `LinkExistsError` — same mechanism `qkeee-erp-procurement`
   and `qkeee-erp-accounts-executive` already confirmed for other
   doctypes, reconfirmed here rather than newly discovered. Test SO
   (`SAL-ORD-2026-00005`) and DN (`MAT-DN-2026-00002`) left in place,
   cancelled; test Customer (`qkeee-sales-test Customer`) left in place,
   `disabled: 1`, since it has a linked Contact and can't be deleted
   ("You can disable this Customer instead of deleting it.").

Temporary API key/secret revoked at the end of the session per the
project's standard.

## Discovering a DocType's real field list (build-time technique)

`GET /api/resource/DocType/<DocType Name>` returns that DocType's live
field definitions (fieldname, fieldtype, `reqd`, `options`, `fetch_from`)
— used throughout this build for Customer, Quotation, Quotation Item,
Sales Order, Sales Order Item, Delivery Note, Delivery Note Item, and
Contact. Prefer this over `docs.frappe.io` for confirming an org's
actual field list/mandatory flags — as this build found twice
(`party_name`, `Customer.mobile_no`/`email_id`), the declared `reqd`
flag and even the raw fieldtype don't always tell the whole practical
story; check `fetch_from` too.

## run_query_report() — built-in reports

`frappe.desk.query_report.run` runs any Report doctype record (Query
Report or Script Report) by name. Confirmed live: **"Sales Order
Analysis"** (Script Report, module Selling) returns per-line
delay/pending-qty/pending-amount data straight off real transactional
Sales Order data already on `<erp-instance>` — no fixtures needed for
this read path. **"Quotation Trends"** exists in the same module but was
**not live-tested this build** — `scripts/render_report.py`'s
`build_pipeline()` hand-aggregates the Quotation-stage side from a plain
`status` query instead (no dedicated built-in report was confirmed to
cover that specific lens), same shape as `qkeee-erp-procurement`'s
RFQ/quotation-comparison gap. Re-check "Quotation Trends" against a
real org before assuming the hand-aggregated path is the only option.

## The read-only/read-write gate

`mutate_resource()` takes `mode` as an explicit parameter (sourced from
`metadata.hermes.config` → `qkeee_erp.mode`) and refuses any
create/update/submit/cancel/delete unless `mode == "read-write"`. This
is the library-wide gate — identical to every other `qkeee-erp-*`
skill's copy. It is **not** the same as this skill's two
capability-specific gates (KYC-completeness for Customer onboarding,
draft-only-always for Quotation) — those are enforced in
`scripts/render_customer_draft.py` / `scripts/render_quotation_draft.py`,
closer to where each draft is built, not in this shared gate.

## Requester attribution and the audit-comment trail

`mutate_resource()` also requires `requested_by` — the ERPNext user
id/email of the human who asked for the change, sourced from
`qkeee_erp.requested_by` — and refuses any write missing it
(`MissingRequesterError`), same enforcement style as the mode gate.
On a successful create/update/submit/cancel/delete it posts a
best-effort Comment onto the affected record via
`frappe.desk.form.utils.add_comment`: `[<SKILL_LABEL>] <action> —
requested by <requested_by>, applied via qkeee-erp bot.` A comment
failure never blocks or rolls back the write it documents. See
`qkeee-erp-core`'s reference for the full mechanism (`record_comment()`).

## Known gaps

- **Submit is fetch-then-submit, not atomic** — same unmitigated race as
  every other `qkeee-erp-*` skill's connector copy; flag an unexpected
  `TimestampMismatchError` on submit rather than treating it as generic.
- **Delivery Note without `so_detail` not independently tested.** This
  build only validated the "both `against_sales_order` and `so_detail`
  supplied" path. Whether `per_delivered` still updates correctly (or
  degrades to an untracked/loose link) with `against_sales_order` alone
  is undetermined — treat any Delivery Note queried without a resolvable
  `so_detail` on its lines as a data-quality flag worth surfacing to the
  user, not a confirmed-safe pattern.
- **Customer's `customer_primary_address` (Address linkage) was not
  live-tested** — only the Contact/primary-contact leg was validated.
  `render_customer_draft.py` doesn't currently stage an Address at all;
  treat billing-address completeness as documentation-grounded only
  until validated against a real instance.

## Query pagination

`query_resource()` requests `limit + 1` rows and trims to `limit`,
returning `{"data": [...], "has_more": bool, "limit": N}`. Always check
`has_more` — a pipeline report that silently drops rows past the default
limit is a bug in the calling report logic, not something the connector
prevents by itself.

## Harness capability discovery

Before assuming this bundled `urllib`-based script is the only option,
check whether the host harness already exposes an HTTP-capable tool and
prefer that. Degrade gracefully to this script if discovery isn't
supported — never hard-fail because discovery itself isn't possible.

## CLI usage

```
python erp_client.py list-envs
python erp_client.py --tag qa health
python erp_client.py --tag qa query Customer --fields '["name","customer_name","territory"]'
python erp_client.py --tag qa query "Sales Order" --filters '[["status","=","To Bill"]]' --fields '["name","status","delivery_status","per_delivered","billing_status","per_billed"]'
python erp_client.py --tag qa report "Sales Order Analysis" --filters '{"company":"Enfasco Inc.","based_on":"Sales Order Date","from_date":"2026-01-01","to_date":"2026-12-31","doctype":"Sales Order"}'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate Customer create --payload-file draft.json  # prefer --payload-file over --payload (shell history/ps exposure)
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate Contact create --payload-file draft.json
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate Quotation create --payload-file draft.json
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate Quotation submit --name "SAL-QTN-2026-00001"
```

## Extension point

To target a different ERP backend, replace `scripts/erp_client.py` and
this file (here and in `qkeee-erp-core`, the source of truth). Nothing
in `references/domain-knowledge.md` or this skill's `SKILL.md` needs to
change — they're written to be ERP-agnostic in substance.

## Audit-trail retrofit (synced from qkeee-erp-core)

`mutate_resource()` wraps every write with a two-phase log to the
`Qkeee Bot Audit Log` doctype (`Attempted` before the real call,
`Success`/`Failure` after), best-effort throughout — a target instance
that hasn't run `qkeee-erp-bot-init` yet keeps writing exactly as before
this retrofit, just unaudited. `query_resource()`/`get_resource()`/
`run_query_report()` gained an opt-in `debug` kwarg (`qkeee_erp.debug`)
for `Read`-row logging, off by default. `AUDIT_EXEMPT_DOCTYPES` prevents
the logger from recursively logging itself or double-logging the audit
Comment write. Full mechanism, decision log, and doctype schema:
`qkeee-erp-core/references/connector-reference.md`'s own "Audit-trail
retrofit" section and `qkeee-erp-bot-init/references/bot-doctypes-
design.md`.
