# qkeee-erp-inventory — ERPNext Stock module doc map

Curated pointers into `docs.frappe.io/erpnext` (Stock module) plus this
build's live field-schema grounding against `<erp-instance>`. Consult at
runtime — fetch the linked page directly if a harness web-fetch tool is
available — whenever an ERPNext-specific mechanic is uncertain.

## DocTypes this skill touches

| DocType | docs.frappe.io section | Submittable? |
| --- | --- | --- |
| Stock Entry | Stock → Stock Entry | Yes |
| Stock Reconciliation | Stock → Stock Reconciliation | Yes |
| Material Request | Stock → Material Request | Yes |
| Bin | (internal, not a doc a user edits directly) | No — system-maintained |
| Batch | Stock → Batch | No |
| Serial No | Stock → Serial No | No |
| Stock Ledger Entry | (internal ledger, read-only via API) | No |
| Item | Stock → Item | No |
| Warehouse | Stock → Warehouse | No |

## Live field-schema grounding (<erp-instance>)

**Stock Entry** — mandatory: `naming_series`, `stock_entry_type`
(Link → Stock Entry Type), `company`, `items` (child table, Stock Entry
Detail). `purpose` is a Select mirroring `stock_entry_type` but is
informational; `stock_entry_type` drives behavior. Confirmed live
Stock Entry Type values on this instance: Material Issue, Material
Receipt, Material Transfer, Material Transfer for Manufacture, Material
Consumption for Manufacture, Manufacture, Repack, Send to Subcontractor,
Disassemble.

**Stock Entry Detail** (child) — mandatory: `item_code`, `qty`, `uom`,
`stock_uom`, `conversion_factor`. `s_warehouse` (source) and
`t_warehouse` (target) are both optional at the schema level, but
**practically required** depending on `stock_entry_type` — a Receipt
needs only `t_warehouse`, an Issue only `s_warehouse`, a Transfer needs
both. Neither is enforced as `reqd` in the DocType schema itself; the
skill's own renderer (`render_stock_entry_draft.py`) enforces "at least
one of s_warehouse/t_warehouse" and, for any `s_warehouse` line, an
availability check ERPNext itself only performs at submit (see
`connector-reference.md`'s Live validation record, finding 3).

**Stock Reconciliation** — mandatory: `naming_series`, `company`,
`purpose` (Select: "Opening Stock" / "Stock Reconciliation"),
`posting_date`, `posting_time`, `items` (child table, Stock
Reconciliation Item).

**Stock Reconciliation Item** (child) — mandatory: `item_code`,
`warehouse`. `qty` and `valuation_rate` are the caller's stated new
count/rate; `current_qty` and `current_valuation_rate` exist on the
schema but are **not** trustworthy from a caller-supplied value at
create time (confirmed live — silently reset to 0 regardless of what
was sent). For any item where `Item.has_batch_no` or
`Item.has_serial_no` is true, `use_serial_batch_fields: 1` is required
on the line or create fails with `Please add Serial and Batch Bundle`
(confirmed live) — and even then, correctness depends on resolving the
real current_qty **per batch** via `get_items` (see
`connector-reference.md`'s `get_stock_reconciliation_items()` section)
rather than at the item level.

**Material Request** — mandatory: `naming_series`,
`material_request_type` (Select: Purchase, Material Transfer, Material
Issue, Manufacture, Customer Provided), `company`, `transaction_date`,
`items` (child table, Material Request Item).

**Material Request Item** (child) — mandatory: `item_code`,
`schedule_date`, `qty`, `stock_uom`, `uom`, `conversion_factor`.
`warehouse` is optional at the schema level but recommended for a
Purchase-type request (this skill's `render_material_request_draft.py`
flags its absence as an issue, not a hard block).

**Batch** — mandatory: `batch_id`, `item`. Read-only from this skill's
perspective (no create/update capability built).

**Serial No** — mandatory: `serial_no`, `item_code`. Fields of interest
for a trace: `warehouse`, `status` (`Active`/`Delivered`/etc.),
`batch_no`.

**Item** (fields consulted, not created/updated by this skill) —
`is_stock_item`, `has_batch_no`, `has_serial_no`, `stock_uom`,
`valuation_rate` (item-level default; NOT the same as a specific
warehouse's current valuation — use `Bin`/`get_items` for that).

**Warehouse** (fields consulted) — `warehouse_name`, `company`,
`is_group` (a group warehouse is a container for other warehouses, not
a postable stock location itself — filter `is_group: 0` when presenting
"which warehouse should I pick" choices to a user).

## Bin — the live-balance table

Not a doc a user creates or edits — ERPNext maintains one `Bin` row per
(item_code, warehouse) automatically as Stock Ledger Entries post.
`GET /api/resource/Bin?filters=[["item_code","=","..."],["warehouse","=","..."]]
&fields=["item_code","warehouse","actual_qty"]` is the standard read
path (wrapped by `erp_client.get_bin_qty()`). Reliable for non-batch
items; for a batch-tracked item, `Bin.actual_qty` is the item-level
total but doesn't show the per-batch split — use `get_items` for that.

## Stock Ledger Entry — the audit trail

Every stock-affecting transaction (Stock Entry, Stock Reconciliation,
Delivery Note, Sales Invoice, Purchase Receipt, etc.) posts one or more
`Stock Ledger Entry` rows. Key fields for a trace: `item_code`,
`warehouse`, `posting_date`, `actual_qty` (the movement — positive in,
negative out), `qty_after_transaction` (the running balance right after
this entry), `voucher_type`/`voucher_no` (what caused it), `batch_no`.
Confirmed live: a cancelled document's forward and reversing legs are
both present in the ledger and net to exactly zero — useful for
verifying a cancel fully undid its effect, as this build did to confirm
the batch-inflation test (`MAT-RECO-2026-00002`) was cleanly reversed.

## The `get_items` whitelisted method

`erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.
get_items` — the same server-side logic ERPNext's own "Get Items"
button (in the Stock Reconciliation form) calls. Requires `warehouse`,
`posting_date`, `posting_time`, `company`; `item_code` narrows to one
item (omitting it returns every item with a nonzero/tracked balance in
that warehouse — not exercised in this build, but documented in
ERPNext's own source for that method). Returns a list under
`{"message": [...]}`, one row per item (or per batch, for a
batch-tracked item), each with `current_qty`/`current_valuation_rate`/
`batch_no`. This is the authoritative source `erp_client.
get_stock_reconciliation_items()` wraps — see `connector-reference.md`
for the full mechanism and why it matters.

## Version note

Confirmed live: Frappe Framework 15.110.0 / ERPNext 15.110.0 on
`<erp-instance>` (via `last_known_versions` on the
Administrator User doc). Field lists/behaviors above are grounded
against this version; re-verify via `GET /api/resource/DocType/<name>`
against a target org's actual instance before relying on them, per the
build-time technique noted in `connector-reference.md`.
