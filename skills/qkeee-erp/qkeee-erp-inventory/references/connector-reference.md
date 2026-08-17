# qkeee-erp-inventory connector reference

This skill's copy of the `qkeee-erp` connector layer, synced from the
canonical version in `qkeee-erp-core/references/connector-reference.md`.
Carries the full read+write path (`mutate_resource`) plus two
inventory-specific additions: `get_bin_qty()` (live on-hand balance) and
`get_stock_reconciliation_items()` (ERPNext's own authoritative
current-qty/batch resolver, wrapping the `get_items` whitelisted
method).

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
| Live Bin balance | GET | `/api/resource/Bin?filters=...&fields=["item_code","warehouse","actual_qty"]` |
| Authoritative reconciliation current-qty/batch resolver | POST | `/api/method/erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_items` |
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

**Submit is two calls, not one** — see `mutate_resource()`'s docstring
and `qkeee-erp-core`'s canonical reference for why (mandatory-field
validation needs the full DB-loaded doc, not a sparse payload).

**Response shape differs by action.** `create`/`update`/the GET before
submit return `{"data": {...doc...}}`. `submit` and `cancel`
(whitelisted RPC methods, not REST resource calls) return `{"message":
{...doc...}}` instead. `get_items` (used by
`get_stock_reconciliation_items()`) also returns its payload under
`{"message": [...]}` — a list, not a dict, confirmed live. Code must
check for the right key and shape per endpoint, never assume
`["data"]` unconditionally.

## Live validation record

**Full round trip against `<erp-instance>` confirmed**, using
a temporary API key/secret (generated via session login +
`frappe.core.doctype.user.user.generate_keys`, per admin credentials the
org provided for this validation pass). Company `Qkeee LLP`, warehouses
`Stores - QL` / `Finished Goods - QL`.

1. **Material Receipt** — `mutate create` on Stock Entry (`SKU-1`, qty
   10, `t_warehouse: "Stores - QL"`, `basic_rate: 5`) → `MAT-STE-2026-
   00003`, docstatus 0. `mutate submit` (fetch-then-submit) → docstatus
   1. Bin confirmed 10.0 after.
2. **Material Transfer** — create (`SKU-1`, qty 4, `Stores - QL` →
   `Finished Goods - QL`) → `MAT-STE-2026-00004`, docstatus 0 — accepted
   with NO qty-availability check at create time. Submit → docstatus 1,
   Bin correctly split (6.0 remaining in Stores, 4.0 in Finished Goods).
3. **Insufficient-stock finding** — a second Material Transfer drafted
   with qty 999 against a 6.0 balance was **accepted as a Draft with no
   error** (`MAT-STE-2026-00006`, docstatus 0). Only `mutate submit`
   rejected it, with `ValidationError: For the item SKU-1, the
   Available qty 6.0 is less than the Required Qty 999.0 in the
   warehouse Stores - QL`. **ERPNext does not enforce available-qty at
   draft-create time — only at submit.** This is why
   `scripts/render_stock_entry_draft.py` performs its own
   availability check against a freshly-fetched Bin balance before
   staging any outgoing line as "ready."
4. **Stock Reconciliation — the batch-tracking inflation trap.**
   `SKU-1` is both batch- and serial-tracked
   (`has_batch_no: 1, has_serial_no: 1` — confirmed live, unexpectedly,
   since it wasn't obviously named as such). A first attempt to
   reconcile `SKU-1` in `Stores - QL` to qty 8 with a bare payload
   failed at create: `ValidationError: Row # 1: Please add Serial and
   Batch Bundle for Item SKU-1`. Retried with `use_serial_batch_fields:
   1` — accepted, but with `current_qty` silently reset to `0.0` in the
   response regardless of what was passed. Submitted anyway (docstatus
   1) — **Bin balance went to 14.0, not 8.0**: the actual 6.0 balance
   plus the stated 8, ADDED rather than SET, because the reconciliation
   had no correct current_qty/batch_no to correct against and instead
   created a fresh 8-unit batch. **This is the most important live
   finding of this build** — see `get_stock_reconciliation_items()`
   below and `render_reconciliation_draft.py`'s docstring for the full
   mechanism and the mitigation. Cancelled to restore the correct 6.0
   balance (Stock Ledger Entry trace confirmed the cancel fully
   reversed both legs — net zero).
5. **Stock Reconciliation — clean path on a non-batch item.**
   `Raw Item-1` (not batch/serial-tracked) reconciled from 0.0 to 20.0
   at valuation_rate 2, with `current_qty: 0` passed explicitly — create
   succeeded without needing `use_serial_batch_fields`, submit
   succeeded, **Bin correctly SET to exactly 20.0** (absolute, not
   additive). Confirms: for a non-batch item, ERPNext's reconciliation
   is safe regardless of what `current_qty` says — the risk is
   batch-tracked items specifically.
6. **`get_items` whitelisted method confirmed as the authoritative
   resolver.** `POST .../get_items` with `{warehouse, posting_date,
   posting_time, company, item_code}` returned, for `SKU-1`, ONE ROW PER
   EXISTING BATCH (`SKU1-00007`: current_qty 6.0; `SKU1-00018`: current_qty
   8.0 — pre-existing from a prior session's data), each with its own
   `current_qty`/`current_valuation_rate`/`batch_no`. For `Raw Item-1`
   (non-batch), it returned a single row matching the live Bin exactly
   (`current_qty: 0.0`). This confirms `get_items` — not `Bin`, not a
   caller-supplied guess — is the only reliable source for a
   reconciliation line's `current_qty`, especially for a batch-tracked
   item where it's the only way to see the per-batch breakdown at all.
7. **Material Request** — Purchase-type draft (`Raw Item-2`, qty 50,
   `schedule_date: "2026-08-20"`, `warehouse: "Stores - QL"`) created
   (`MAT-MR-2026-00004`, status "Draft"), submitted (status "Pending"),
   cancelled (cleanup) — full round trip confirmed clean, no
   surprises.
8. **Batch/Serial trace grounding.** `Serial No` query confirmed
   per-serial `status` (`Active`/`Delivered`) and `warehouse` fields;
   `Stock Ledger Entry` query confirmed `qty_after_transaction` gives a
   running balance per item/warehouse, and that a cancelled document's
   two ledger legs (forward + reversing) net to zero exactly — used to
   independently verify step 4's cancel fully undid the inflation.

**Post-review fix — why the `remarks` label didn't persist
on the two left-submitted Stock Entries.** Confirmed live against
`<erp-instance>`: Stock Entry's `remarks` field has `allow_on_submit: 0`
in its DocField definition — Frappe silently drops (or rejects) any PUT
to a field without `allow_on_submit` once `docstatus` is 1. The prior
build's PUT ran *after* submit, which is why `MAT-STE-2026-00003`'s
`remarks` came back `null` on re-query. Any future cleanup labeling must
set `remarks` on the Stock Entry draft **before** the create→submit
round trip, not after.

Test data left in place per the library's established convention
(ledger-touching chains aren't fully unwound — same `LinkExistsError`
reasoning as prior builds): `MAT-STE-2026-00003` and `MAT-STE-2026-00004`
(both submitted, labeled via `remarks: "qkeee-erp-inventory connector
validation - safe to reverse"`), `MAT-RECO-2026-00003` (submitted,
labeled via `remarks`, same text). `MAT-RECO-2026-00002` (the batch
inflation test) was cancelled, not left submitted, since it represented
a genuinely wrong balance. `MAT-MR-2026-00004` was cancelled as part of
its own round-trip test. Temporary API key/secret revoked immediately
after validation (`PUT /api/resource/User/Administrator` with
`{"api_key": null}`, then reconfirmed the old token 401s).

## Discovering a DocType's real field list (build-time technique)

`GET /api/resource/DocType/<DocType Name>` returns that DocType's live
field definitions — used throughout this build for Stock Entry, Stock
Entry Detail, Stock Reconciliation, Stock Reconciliation Item, Material
Request, Material Request Item, Batch, Serial No, Item, and Warehouse.
Prefer this over `docs.frappe.io` for confirming an org's actual field
list/mandatory flags — as this build found twice (the Stock Entry
warehouse requirement precedent from `qkeee-erp-procurement`'s build,
and this build's own Serial/Batch Bundle requirement), the declared
`reqd` flag isn't always the whole story; `validate()`-time requirements
can be stricter than the schema alone shows.

## The read-only/read-write gate

`mutate_resource()` takes `mode` as an explicit parameter (sourced from
`metadata.hermes.config` → `qkeee_erp.mode`) and refuses any
create/update/submit/cancel/delete unless `mode == "read-write"`. This
is the library-wide gate — identical to every other `qkeee-erp-*`
skill's copy. It is **not** the same as this skill's own gates
(available-qty checking for transfers, current-qty/batch resolution for
reconciliations) — those are enforced in `scripts/render_stock_entry_
draft.py` / `scripts/render_reconciliation_draft.py`, closer to where
each draft is built, not in this shared gate.

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

## get_bin_qty() — live on-hand balance

Wraps `GET /api/resource/Bin`. Authoritative for a non-batch/non-serial
item's on-hand qty at a warehouse. For a batch-tracked item, prefer
`get_stock_reconciliation_items()` to also see the per-batch breakdown
before staging a reconciliation — `Bin.actual_qty` alone doesn't show
which batch holds how much.

## get_stock_reconciliation_items() — the reconciliation resolver

Wraps `POST .../stock_reconciliation.get_items`. **Always call this
before staging a Stock Reconciliation draft — never guess or hand-supply
`current_qty`.** For a batch-tracked item, returns one row per existing
batch; the caller must resolve/preserve each batch's own `batch_no` and
`current_qty` when building the reconciliation's line items, or risk the
inflation trap documented in the Live validation record above. See
`scripts/render_reconciliation_draft.py`'s docstring for the full
mechanism and how the renderer enforces this at the drafting stage.

`item_code` is optional (confirmed live: `POST .../get_items`
with `{warehouse, posting_date, posting_time, company}` and no
`item_code` returned every item with a balance in that warehouse — 3
rows for `Stores - QL` on this instance). Use this to resolve a whole
warehouse's physical count in one call instead of one per item.

## get_bin_qty() → actual_source_qty translation

`get_bin_qty()` returns `{"data": [{"item_code", "warehouse",
"actual_qty"}, ...]}` — a list of rows. `render_stock_entry_draft()`'s
`actual_source_qty` parameter wants a `{(item_code, warehouse): qty}`
mapping. Use `erp_client.bin_rows_to_actual_source_qty(get_bin_qty(...)
["data"])` to convert — don't hand-glue this at the call site; the shape
mismatch was previously undocumented and would fail every availability
check with "no actual_source_qty entry provided" even when stock was
genuinely available, just because the shapes never matched.

## Known gaps

- **Submit is fetch-then-submit, not atomic** — same known gap as every
  other `qkeee-erp-*` skill's connector copy; no optimistic-lock support
  in the plain Frappe REST resource API. Flag an unexpected
  `TimestampMismatchError` on submit rather than treating it as a
  generic write failure.
- **Batch/Serial Bundle mechanics beyond Stock Reconciliation weren't
  exhaustively probed.** Stock Entry accepted batch/serial-tracked
  `SKU-1` without an explicit Serial and Batch Bundle payload (the
  `use_serial_batch_fields` requirement was only observed on Stock
  Reconciliation, not Stock Entry, in this build) — if a target org's
  ERPNext version behaves differently for Stock Entry on a batch/serial
  item, treat any unexpected "Please add Serial and Batch Bundle" error
  the same way this build treated it on Reconciliation: don't guess a
  workaround, investigate the real required fields for that call.
- **Renderers trust caller-supplied data, by design.**
  `render_stock_entry_draft.py` and `render_reconciliation_draft.py`
  never call ERPNext themselves — they format and gate on whatever
  `actual_source_qty`/`batch_tracked_items`/current-qty data the caller
  passes in. This is intentional (pure formatting, no side effects, no
  network access), but it means the freshness and correctness of that
  data is the calling agent's responsibility, not the renderer's. Always
  fetch balances immediately before drafting, not from an earlier point
  in the conversation.
- **No create-time idempotency.** Frappe's REST API has no
  idempotency-key mechanism. A `create` call that times out and is
  retried by the caller can produce a duplicate record. There's no
  connector-level protection against this — treat a timeout on `create`
  as ambiguous (check whether the record was actually created before
  retrying), not as a safe-to-retry failure.
- **Supplier Scorecard / put-away rules / advanced batch-expiry logic
  are present in the live schema but out of this skill's built
  capability table** — noted as a possible future extension, not a gap
  in what was promised to be built.

## Query pagination

`query_resource()` requests `limit + 1` rows and trims to `limit`,
returning `{"data": [...], "has_more": bool, "limit": N}`. Always check
`has_more` — a stock-level query or a batch/serial trace that silently
drops rows past the default limit is a bug in the calling report logic,
not something the connector prevents by itself.

## Harness capability discovery

Before assuming this bundled `urllib`-based script is the only option,
check whether the host harness already exposes an HTTP-capable tool and
prefer that. Degrade gracefully to this script if discovery isn't
supported — never hard-fail because discovery itself isn't possible.

## CLI usage

```
python erp_client.py list-envs
python erp_client.py --tag qa health
python erp_client.py --tag qa query "Item" --filters '[["is_stock_item","=",1]]' --fields '["name","has_batch_no","has_serial_no"]'
python erp_client.py --tag qa bin-qty "SKU-1" --warehouse "Stores - QL"
python erp_client.py --tag qa recon-items "SKU-1" --warehouse "Stores - QL" --company "Qkeee LLP" --posting-date "2026-08-10"
python erp_client.py --tag qa recon-items --warehouse "Stores - QL" --company "Qkeee LLP" --posting-date "2026-08-10"  # item_code omitted: whole warehouse
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Stock Entry" create --payload-file draft.json  # prefer --payload-file over --payload (shell history/ps exposure)
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Stock Entry" submit --name "MAT-STE-2026-00003"
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Stock Reconciliation" create --payload-file draft.json
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Material Request" create --payload-file draft.json
```

## Extension point

To target a different ERP backend, replace `scripts/erp_client.py` and
this file (here and in `qkeee-erp-core`, the source of truth). Nothing
in `references/domain-knowledge.md` or this skill's `SKILL.md` needs to
change — they're written to be ERP-agnostic in substance.

## Audit-trail retrofit

`mutate_resource()` wraps every write with a two-phase log to the
`Qkeee Bot Audit Log` doctype (`Attempted` before the real call,
`Success`/`Failure` after), best-effort throughout — a target instance
that hasn't run `qkeee-erp-bot-init` yet keeps writing exactly as
before this retrofit, just unaudited. `query_resource()`/`get_resource()`
carry an opt-in `debug` kwarg (`qkeee_erp.debug`) for `Read`-row
logging, off by default. `AUDIT_EXEMPT_DOCTYPES` prevents the logger
from recursively logging itself or double-logging the audit Comment
write. Full mechanism, decision log, and doctype schema:
`qkeee-erp-core/references/connector-reference.md`'s own "Audit-trail
retrofit" section and `qkeee-erp-bot-init/references/bot-doctypes-
design.md`.
