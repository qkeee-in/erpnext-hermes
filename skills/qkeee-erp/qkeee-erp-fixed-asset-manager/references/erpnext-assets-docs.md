# qkeee-erp-fixed-asset-manager — ERPNext Assets module reference

Curated map into `docs.frappe.io/erpnext` (Assets module) plus live
field-schema grounding confirmed against `<erp-instance>` (ERPNext
v15.110.0 / Frappe v15.110.0) during this skill's build, 2026-08-10.
Consult the linked docs pages directly (fetch, if a harness web-fetch
tool is available) when a mechanic is uncertain; prefer live
`GET /api/resource/DocType/<name>` introspection over docs when they
disagree — this build found real gaps between the two (see below).

## Doctype map

| DocType | Submittable | Purpose |
| --- | --- | --- |
| `Asset` | Yes | The asset record itself — identity, location, cost, depreciation config |
| `Asset Category` | No | Groups assets for accounting (fixed asset / accumulated depreciation / depreciation expense account mapping) and default finance-book settings |
| `Asset Category Account` | (child table, per-company) | `company_name`, `fixed_asset_account` (reqd), `accumulated_depreciation_account`, `depreciation_expense_account`, `capital_work_in_progress_account` |
| `Asset Finance Book` | No (child table on `Asset`) | Depreciation method/term/start-date per finance book on an Asset |
| `Asset Depreciation Schedule` | **Yes** | A separate, submittable parent doc auto-created when an Asset with `calculate_depreciation=1` is created — holds the actual period-by-period `Depreciation Schedule` child rows. This is NOT the same thing as the `Asset.finance_books` child table; that's the *configuration*, this is the *generated schedule*. |
| `Depreciation Schedule` | No (child table on `Asset Depreciation Schedule`) | `schedule_date`, `depreciation_amount`, `accumulated_depreciation_amount`, `journal_entry` (empty until posted) |
| `Asset Movement` | Yes | Transfer/Issue/Receipt/Transfer and Issue — child table `assets` (Asset Movement Item) carries `source_location`/`target_location`/`from_employee`/`to_employee` per asset |
| `Asset Repair` | Yes | Unplanned repair — `repair_status` (Pending/Completed/Cancelled), optional `capitalize_repair_cost` |
| `Asset Maintenance` | No | Groups a set of `Asset Maintenance Task` child rows for one asset |
| `Asset Maintenance Task` | No (child table) | One recurring task — `periodicity`, `next_due_date`, `maintenance_status` |
| `Asset Maintenance Log` | Yes | One completed/logged maintenance occurrence, linked to a task |

## Field-schema findings from this build (live, not doc-derived)

**`Asset`'s schema-mandatory fields are narrower than a real
capitalization needs**: only `company`, `item_code`, `asset_name`,
`location`, `purchase_date` are `reqd`. `asset_category` is NOT
schema-mandatory — confirmed live, an Asset creates successfully
without one — but without it there's no Asset Category Account mapping
for a depreciation run to post to. This is why
`scripts/render_asset_draft.py`'s bar is stricter than ERPNext's own,
same pattern as every prior `qkeee-erp-*` persona skill's capability
renderer.

**Two distinct doctypes model "depreciation config" vs. "the actual
schedule" — this was NOT obvious from the field names alone.**
`Asset.finance_books` (Asset Finance Book child table) is the
*configuration* an Asset is created with (method, total periods,
frequency, start date). Creating an Asset with `calculate_depreciation:
1` and a populated `finance_books` array auto-generates a **separate**
submittable document, `Asset Depreciation Schedule`, holding the actual
period-by-period rows (`Depreciation Schedule` child table) — confirmed
live via the `_server_messages` returned on Asset create: `"Asset
Depreciation Schedules created: ...ACC-ADS-2026-00001..."`.

**Submitting the Asset auto-submits its Asset Depreciation Schedule.**
Confirmed live: before Asset submit, `ACC-ADS-2026-00001` was `status:
Draft, docstatus: 0`; immediately after `mutate submit` on the Asset,
it was `status: Active, docstatus: 1` — the same submit call, no
separate action needed.

**Depreciation posting is a whitelisted RPC call, not a plain REST
mutate, and posts EVERY overdue period in one call — a significant,
easy-to-miss finding.**

```
POST /api/method/erpnext.assets.doctype.asset.depreciation.make_depreciation_entry
Body: {"asset_depr_schedule_name": "<Asset Depreciation Schedule name>"}
```

Confirmed live: calling this once against a schedule with 6 periods
overdue (start date several months in the past relative to the test
date) posted **six separate Journal Entries in that one call** — every
`Depreciation Schedule` row with `schedule_date <= today` and an empty
`journal_entry` got a JE created and linked in a single request. There
is no "post just the next period" mode via this method — a caller
wanting single-period granularity would need to call it more frequently
(e.g. exactly at each period boundary) rather than letting periods
accumulate. `scripts/render_depreciation_run.py` states the full period
count and total amount for this reason — never call this method without
first fetching and showing every currently-pending row.

`erpnext.assets.doctype.asset.depreciation.post_depreciation_entries`
(the org-wide batch-posting entry point used by ERPNext's scheduled
job) returned `PermissionError: ... is not whitelisted` when called
directly via the REST API as Administrator — it's scheduler-internal,
not a callable RPC endpoint for this connector. Per-asset posting via
`make_depreciation_entry` (above) is the only confirmed-callable path.

**`Asset.value_after_depreciation` (the top-level field) does NOT
update after a depreciation run — confirmed live, a real finding, not a
documentation gap.** After posting 6 periods (each 100 of depreciation
on a 1200 asset), the top-level field still read `1200.0`. The real
current book value lives on `Asset.finance_books[N]
.value_after_depreciation` (read `600.0` after the same run) alongside
`finance_books[N].total_number_of_booked_depreciations` (read `6`).
Any capability reporting an asset's current value after a run must read
the finance_books child table, never the top-level field — encoded in
`scripts/render_depreciation_run.py`'s docstring and inline comments so
this doesn't get silently reintroduced later.

**Scrap and restore are whitelisted RPC calls under a specific,
version-dependent module path — NOT `asset.scrap_asset` /
`asset.restore_asset` as older ERPNext documentation/community threads
suggest.** Confirmed live on this version:

```
POST /api/method/erpnext.assets.doctype.asset.depreciation.scrap_asset
Body: {"asset_name": "<Asset name>"}

POST /api/method/erpnext.assets.doctype.asset.depreciation.restore_asset
Body: {"asset_name": "<Asset name>"}
```

`scrap_asset` sets `Asset.status` to `Scrapped`, `docstatus` stays `1`
(the Asset itself is not cancelled — scrapping is a status change plus
an auto-created Journal Entry, not a cancel), and populates
`journal_entry_for_scrap` with the new JE's name. Confirmed live:
`"Asset scrapped via Journal Entry ACC-JV-2026-00010"`. Scrapping the
Asset also auto-cancels its active `Asset Depreciation Schedule` and
creates a new (amended) one — confirmed live: the original schedule's
`docstatus` went `1 -> 2` (Cancelled) and a new schedule doc appeared.

**Sale disposal uses a different whitelisted method, drafts a Sales
Invoice, and does NOT itself realize the gain/loss:**

```
POST /api/method/erpnext.assets.doctype.asset.asset.make_sales_invoice
Body: {"asset": "<Asset name>", "item_code": "<Item code>", "company": "<Company>"}
```

Not live-executed to completion in this build (would require a
Customer and full Sales Invoice submission flow, out of this skill's
own scope — Sales Invoice submission belongs to whoever owns that
document type, conceptually `qkeee-erp-sales`/`qkeee-erp-accounts-
executive`). `scripts/render_disposal.py`'s sale path is documentation/
signature-grounded (the method's required-argument error, confirmed
live, revealed the exact signature) rather than a full live round trip
— flag this to a user relying on the sale-disposal path for the first
time against a new instance.

**Asset Movement's `source_location` is NOT cross-checked by ERPNext
against the asset's actual current `location`.** Confirmed live: a
Transfer created and submitted with a correct source_location worked
and updated `Asset.location` to the target — but nothing in the
create/submit path was observed to reject a *fabricated* source. This
is exactly why `scripts/render_movement_draft.py` performs that check
itself, using a freshly-fetched `Asset.location` value the caller must
supply.

**Asset Repair's create -> update(status=Completed, cost) -> submit
round trip works cleanly** — confirmed live, no surprises: creating
with `repair_status: "Pending"`, then updating to `"Completed"` with a
`repair_cost`, then submitting, all succeeded in sequence.

**Fiscal Year matters for Asset creation dates.** Confirmed live: an
Asset with `purchase_date`/`available_for_use_date` outside any active
Fiscal Year fails create with `FiscalYearError`, not a generic
validation error — surface this specific error to the user rather than
a bare "create failed" if it occurs.

## Live validation record

Full round trip confirmed against `<erp-instance>`, 2026-08-10, using a
temporary API key/secret (session login + `generate_keys`, revoked
immediately after):

1. Test fixtures created: Asset Category `qkeee-fam-test Equipment`
   (mapped to `Capital Equipments - QL` / `Accumulated Depreciation -
   QL` / `Depreciation - QL` on company `Qkeee LLP`), Location
   `qkeee-fam-test Location` (+ a second for transfer testing), Item
   `QKEEE-FAM-TEST-LAPTOP` (`is_fixed_asset: 1`).
2. Asset `ACC-ASS-2026-00001` created (gross amount 1200, Straight
   Line, 12 periods, 1/period, start 2026-02-28) -> submitted
   (`docstatus 0 -> 1`, `status: "Submitted"`) -> its Asset Depreciation
   Schedule `ACC-ADS-2026-00001` auto-submitted alongside it.
3. Asset Movement (`Transfer`, source `qkeee-fam-test Location` ->
   target `qkeee-fam-test Location 2`) created and submitted;
   `Asset.location` confirmed updated to the target. Movement cancelled
   afterward (cleanup).
4. `scrap_asset("ACC-ASS-2026-00001")` called — `status -> Scrapped`,
   `journal_entry_for_scrap: "ACC-JV-2026-00010"`, original ADS
   auto-cancelled, a new (amended) ADS created.
5. Second Asset `ACC-ASS-2026-00002` created (same config, purchase
   2026-01-01) -> submitted -> `ACC-ADS-2026-00003` created active.
   `make_depreciation_entry("ACC-ADS-2026-00003")` called once — posted
   6 periods (Feb-Jul 2026) in that single call, 6 Journal Entries
   created (`ACC-JV-2026-00011` through `...00016`), `finance_books[0]
   .value_after_depreciation` confirmed at `600.0` /
   `total_number_of_booked_depreciations: 6` afterward; top-level
   `value_after_depreciation` confirmed still `1200.0` (the stale-field
   finding above).
6. Asset Repair `ACC-ASR-2026-00001` created (Pending) -> updated
   (Completed, cost 50) -> submitted -> cancelled (cleanup).

Test records left in place, cancelled where cancellation was possible
(Movement, Repair); Assets/Depreciation Schedules/Journal Entries left
as-is (same convention as prior builds — cancelling a ledger-touching
chain fully wasn't attempted, consistent with the `LinkExistsError`
finding already documented across this library). All labeled via
`user_remark: "qkeee-erp-fixed-asset-manager connector validation -
safe to delete"`. Temporary API key/secret revoked immediately after
(`mutate_resource("User", "update", ..., {"api_key": null})` via the
token-auth path itself — the cookie-session PUT path used by earlier
skill builds hit a 403/CSRF issue on this attempt; token-auth update
worked and was reconfirmed by the old token 401ing afterward).

## Not live-tested — flagged as documentation-grounded only

- **Asset Maintenance / Asset Maintenance Task / Asset Maintenance
  Log** — schema confirmed live, but no live create/submit round trip
  was performed (out of build-time scope after the higher-priority
  capitalize/depreciate/transfer/repair/scrap chain). Treat the first
  real maintenance-scheduling use against a new instance as the
  effective validation.
- **Sale disposal (`make_sales_invoice` through to a submitted Sales
  Invoice with realized gain/loss)** — signature confirmed live, full
  round trip not completed (see above).
- **Composite assets, asset splitting (`split_from`), CWIP accounting**
  — present in the schema (`is_composite_asset`, `split_from`,
  `enable_cwip_accounting` on Asset Category) but not exercised in this
  build; not currently in this skill's built capability table.

## Docs pointers

- `docs.frappe.io/erpnext/asset-accounting` — module overview
- `docs.frappe.io/erpnext/asset` — Asset doctype
- `docs.frappe.io/erpnext/asset-category` — Asset Category
- `docs.frappe.io/erpnext/asset-movement` — Asset Movement
- `docs.frappe.io/erpnext/asset-repair` — Asset Repair
- `docs.frappe.io/erpnext/asset-maintenance` — Asset Maintenance

Treat live `GET /api/resource/DocType/<name>` introspection as more
authoritative than these pages for an org's actual configured
behavior — this build found real gaps (the scrap/restore method path,
the stale top-level value field) that generic docs either didn't cover
or covered ambiguously.
