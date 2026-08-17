# qkeee-erp-procurement connector reference

This skill's copy of the `qkeee-erp` connector layer, synced from the
canonical version in `qkeee-erp-core/references/connector-reference.md`.
Carries the full read+write path (`mutate_resource`) plus one
procurement-specific addition, `get_user_roles()`, used as a (heuristic)
signal for PO-submission-authority detection.

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
| Fetch a user's roles (PO-authority heuristic) | GET | `/api/resource/User/<user>` — reads the `roles` child table off the User doc |
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
validation needs the full DB-loaded doc, not a sparse payload). This
also means submit reposts every stored field verbatim, including any
sensitive fields already on the record — expected, not a scope leak;
see `qkeee-erp-core`'s reference for the full note (flagged during
`qkeee-erp-hr-associate`'s 2026-08-10 adversarial review).

**Response shape differs by action.** `create`/`update`/the GET before
submit return `{"data": {...doc...}}`. `submit` and `cancel`
(whitelisted RPC methods, not REST resource calls) return `{"message":
{...doc...}}` instead — confirmed live during this skill's own PO
round trip (see below), consistent with the finding first made during
`qkeee-erp-accounts-executive`'s build. Code built on `mutate_resource()`
must check for either key, never assume `["data"]` unconditionally.

**`delete` behavior differs by whether the doctype is submittable.**
Supplier is **not** a submittable doctype (`docstatus` stays `0`
permanently, no submit concept at all) — confirmed live: a
never-referenced test Supplier deleted cleanly (`DELETE` → `{"data":
"ok"}`, HTTP 202). Purchase Order **is** submittable, and — consistent
with the ledger-touching-doctype finding from `qkeee-erp-accounts-
executive`'s build — `delete` on anything ever submitted should be
expected to fail once linked records exist (not independently
re-confirmed for Purchase Order in this build, but the same
`LinkExistsError` mechanism applies to any submittable doctype with
downstream references; describe **cancel**, not delete, as the
practical undo for a submitted PO).

## Live validation record

**Full create → submit → cancel round trip against `<erp-instance>`
confirmed 2026-08-10**, using a temporary API key/secret (generated via
session login + `frappe.core.doctype.user.user.generate_keys`, per admin
credentials the org provided for this validation pass):

1. `mutate create` on Purchase Order (supplier `Mauli Tea`, company
   `Qkeee LLP`, 1 line `Raw Item-1` qty 5 @ rate 10) — first attempt
   without `warehouse` on the line failed live with `ValidationError:
   Row #1: Warehouse is mandatory for stock Item Raw Item-1`, the
   finding now encoded in `scripts/render_po_draft.py`. Retried with
   `warehouse: "Stores - QL"` — succeeded, `PUR-ORD-2026-00007`,
   `docstatus: 0`, `status: "Draft"`, `total: 50.0`.
2. `mutate submit` — two-step fetch-then-submit path confirmed working,
   `docstatus: 1`, `status: "To Receive and Bill"`.
3. `mutate cancel` — `docstatus: 2`, `status: "Cancelled"`.
4. Separately, `mutate create` on Supplier with only the schema-
   mandatory fields (`supplier_name`, `supplier_type`) plus
   `supplier_group`/`country` succeeded immediately — confirming
   ERPNext's own bar is looser than this skill's KYC bar (this skill's
   `render_supplier_draft.py` would have flagged the same draft
   `incomplete` for missing bank details and tax_id, and refused to
   recommend it as create-ready, even though ERPNext itself accepted
   it). Deleted cleanly afterward (never referenced downstream).

Test PO left in place, cancelled, labeled via `user_remark:
"qkeee-erp-procurement connector validation - safe to delete"` — same
convention every prior `qkeee-erp-*` build has used. Temporary API
key/secret revoked immediately after validation (`PUT /api/resource/
User/Administrator` with `{"api_key": null}`, then reconfirmed the old
token 401s).

## Discovering a DocType's real field list (build-time technique)

`GET /api/resource/DocType/<DocType Name>` returns that DocType's live
field definitions (fieldname, fieldtype, `reqd`, `options`) — used
throughout this build for Supplier, Purchase Order, Purchase Order Item,
Request for Quotation, Supplier Quotation, Purchase Receipt, Purchase
Receipt Item, Purchase Invoice Item, Material Request, and Supplier
Scorecard. Prefer this over `docs.frappe.io` for confirming an org's
actual field list/mandatory flags — and, as this build found with the
warehouse requirement, the declared `reqd` flag isn't always the whole
story; `validate()`-time requirements can be stricter than the schema
alone shows.

## The read-only/read-write gate

`mutate_resource()` takes `mode` as an explicit parameter (sourced from
`metadata.hermes.config` → `qkeee_erp.mode`) and refuses any
create/update/submit/cancel/delete unless `mode == "read-write"`. This
is the library-wide gate — identical to every other `qkeee-erp-*`
skill's copy. It is **not** the same as this skill's two
capability-specific gates (KYC-completeness for Supplier onboarding,
submission-authority default for Purchase Order) — those are enforced
in `scripts/render_supplier_draft.py` / `scripts/render_po_draft.py`,
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

- **Submit is fetch-then-submit, not atomic.** `mutate_resource()`'s
  `submit` action does a GET followed by a POST; there's no optimistic-
  lock/If-Match support in the plain Frappe REST resource API, so a
  record edited by someone else between the two calls could get
  submitted with a stale `full_doc`. Not mitigated in code — flag an
  unexpected `TimestampMismatchError` on submit to the user rather than
  treating it as a generic write failure.
- **The Supplier KYC bar's bank-account field was previously wrong.**
  An earlier version of `scripts/render_supplier_draft.py` required
  three invented field names (`bank_account_name`,
  `bank_account_iban_or_number`, `bank_name`) that don't exist on the
  Supplier doctype. Fixed to require the real field,
  `default_bank_account` (Link -> "Bank Account") — see
  `references/erpnext-buying-docs.md`'s field grounding. The linked
  Bank Account record's own fields (account number, IBAN, bank name)
  are not created or validated by this skill; the caller must
  resolve/create that record first.

## get_user_roles() — PO-authority heuristic, not a guarantee

Fetches the `roles` child table off a User record. Useful as one input
to whether a user plausibly holds PO-submission authority (membership
in `Purchase Manager`/`Purchase Master Manager` vs. only `Purchase
User`), but **no Workflow doctype was found configured for Purchase
Order on `<erp-instance>`** — confirmed via `GET /api/resource/Workflow?
filters=[["document_type","=","Purchase Order"]]` returning empty. On
an instance where a real approval Workflow *is* configured, role
membership alone is an incomplete signal — check for a Workflow first
(`query_resource(tag, "Workflow", filters=[["document_type","=",
"Purchase Order"]])`) and prefer it over the role heuristic when one
exists.

## Query pagination

`query_resource()` requests `limit + 1` rows and trims to `limit`,
returning `{"data": [...], "has_more": bool, "limit": N}`. Always check
`has_more` — a GRN-matching pull or a quotation comparison that silently
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
python erp_client.py --tag qa query "Purchase Order" --filters '[["status","=","To Bill"]]' --fields '["name","status","supplier","per_received","per_billed"]'
python erp_client.py --tag qa query "Workflow" --filters '[["document_type","=","Purchase Order"]]'
python erp_client.py --tag qa roles
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Supplier" create --payload '{"...": "..."}'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Purchase Order" create --payload '{"...": "..."}'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Purchase Order" submit --name "PUR-ORD-2026-00007"
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Purchase Order" cancel --name "PUR-ORD-2026-00007"
```

## Extension point

To target a different ERP backend, replace `scripts/erp_client.py` and
this file (here and in `qkeee-erp-core`, the source of truth). Nothing
in `references/domain-knowledge.md` or this skill's `SKILL.md` needs to
change — they're written to be ERP-agnostic in substance.

## Audit-trail retrofit (synced from qkeee-erp-core, added 2026-08-16)

`mutate_resource()` now wraps every write with a two-phase log to the
`Qkeee Bot Audit Log` doctype (`Attempted` before the real call,
`Success`/`Failure` after), best-effort throughout — a target instance
that hasn't run `qkeee-erp-bot-init` yet keeps writing exactly as before
this retrofit, just unaudited. `query_resource()`/`get_resource()`
gained an opt-in `debug` kwarg (`qkeee_erp.debug`) for `Read`-row
logging, off by default. `AUDIT_EXEMPT_DOCTYPES` prevents the logger
from recursively logging itself or double-logging the audit Comment
write. Full mechanism, decision log, and doctype schema:
`qkeee-erp-core/references/connector-reference.md`'s own "Audit-trail
retrofit" section and `qkeee-erp-bot-init/references/bot-doctypes-
design.md`.
