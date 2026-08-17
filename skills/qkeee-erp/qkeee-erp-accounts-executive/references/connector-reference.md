# qkeee-erp-accounts-executive connector reference

This skill's copy of the `qkeee-erp` connector layer, synced from the
canonical version in `qkeee-erp-core/references/connector-reference.md`.
Unlike `qkeee-erp-mis-analyst`'s trimmed copy, this one carries the full
read+write path (`mutate_resource`) — this persona performs gated writes
(Journal Entry drafting, and live e-invoicing/e-way-bill calls where the
org has India Compliance installed and enabled).

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

Same tagged model as every `qkeee-erp-*` skill — see
`qkeee-erp-core`'s reference for the full table. At install, only
`QKEEE_ERP_DEFAULT_BASE_URL`/`_API_KEY`/`_API_SECRET` are prompted for
(tag `DEFAULT`); adding a second/third environment is a runtime action.

## Endpoints used

| Purpose | Method | Path |
| --- | --- | --- |
| Health check | GET | `/api/method/frappe.auth.get_logged_user` |
| Query a DocType | GET | `/api/resource/<DocType>?filters=...&fields=...&limit_page_length=...` |
| Run a built-in report | POST | `/api/method/frappe.desk.query_report.run` — `{"report_name": "...", "filters": {...}}`. Read-only in effect. |

**`run_query_report()` is a deliberate, reasoned exemption from the
`qkeee_erp.mode` gate, not an oversight.** It's a POST call that never
routes through `mutate_resource()`, so the read-only/read-write check
never applies to it — worth stating explicitly, since the skill's
non-negotiable ("never issue a write call while read-only") could
otherwise read as a blanket policy on every POST. The exemption holds
because `frappe.desk.query_report.run` only executes a server-side report
query and returns rows/columns; it creates, updates, submits, cancels,
and deletes nothing. If a future report-running endpoint ever has a
side effect (some custom report scripts can, in principle, trigger
writes), route it through `mutate_resource()` instead — this exemption
is scoped to genuinely read-only report execution, not "any POST that's
convenient."
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
`qkeee-erp-hr-associate`'s adversarial review).

**Response shape differs by action.**
`create`/`update`/the GET before submit return `{"data": {...doc...}}`.
`submit` and `cancel` (whitelisted RPC methods, not REST resource calls)
return `{"message": {...doc...}}` instead. Code built on
`mutate_resource()`'s return value must check for either key rather than
assuming `["data"]` — confirmed the hard way while validating this
skill's connector copy (a naive `result["data"]` after `submit` raised
`KeyError`; `["message"]` had the doc).

**`delete` fails on anything ever submitted, even after `cancel`** — a
cancelled voucher still has a linked GL Entry and ERPNext refuses the
delete with `LinkExistsError` to protect referential integrity:
`Cannot delete or cancel because Journal
Entry ... is linked with GL Entry ...`. Describe **cancel**, not delete,
as the practical "undo" for anything ledger-touching in domain guidance
and to users — never promise delete will work post-submission.

## Live validation record

**Full create → submit → cancel round trip against `<erp-instance>`
confirmed live**, using a temporary API key/secret (generated via
session login + `frappe.core.doctype.user.user.generate_keys`, per admin
credentials the org provided for this validation pass). Created a
balanced Journal Entry (`Cash - QL` debit 100 / `Administrative Expenses
- QL` credit 100, company `Qkeee LLP`) via `mutate create`, confirmed
`docstatus: 0` and `total_debit == total_credit == 100`; submitted via
`mutate submit`, confirmed the two-step fetch-then-submit path worked;
cancelled via `mutate cancel`, confirmed `docstatus: 2`; confirmed
`delete` on the cancelled record failed with `LinkExistsError` as
expected. The read-only gate was also reconfirmed: `mutate create` with
`--mode read-only` refused with a specific `ReadOnlyModeError` message
before any HTTP call was made. Test record left in place, cancelled,
labeled via `user_remark: "qkeee-erp-accounts-executive connector
validation - safe to delete"` — same convention `qkeee-erp-core`'s
original validation used.

**No India Compliance app installed on `<erp-instance>`** (confirmed via
`Module Def` query — only stock `frappe`/`erpnext`/`hrms`/`crm` modules
present, no GST/India Compliance module). Supplier's `tax_id` field is a
generic Data field, not a dedicated GSTIN field with format validation —
confirmed via `GET /api/resource/DocType/Supplier` field introspection.
**GST/e-invoicing/e-way-bill/GSTR capabilities in this skill could not be
live-validated against this instance** — they're documented from
`docs.indiacompliance.app` (see `references/erpnext-accounting-docs.md`)
but remain unverified end-to-end. Confirm against an instance with India
Compliance installed before treating those capabilities as field-tested.

## Discovering a DocType's real field list (build-time technique)

`GET /api/resource/DocType/<DocType Name>` returns that DocType's live
field definitions (fieldname, fieldtype, `reqd`, `options`) — confirmed
working live for `Supplier` during this build (see above). Prefer this
over `docs.frappe.io` for confirming an org's actual field list/mandatory
flags, since docs describe the general shape, not a specific instance's
customizations.

## The read-only/read-write gate

`mutate_resource()` takes `mode` as an explicit parameter (sourced from
`metadata.hermes.config` → `qkeee_erp.mode`) and refuses any
create/update/submit/cancel/delete unless `mode == "read-write"`. This is
the library-wide gate. It is **not** the same as this skill's
capability-specific advisory-first rules (Journal Entry submission always
needs a separate explicit user confirm regardless of mode — see
`SKILL.md`'s non-negotiable and `scripts/render_je_draft.py`) — those are
enforced closer to where the draft is built, not in this shared gate.

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

## Query pagination

`query_resource()` requests `limit + 1` rows and trims to `limit`,
returning `{"data": [...], "has_more": bool, "limit": N}`. Always check
`has_more` — an aging report or 3-way-match pull that silently drops rows
past the default limit is a bug in the calling report logic, not
something the connector prevents by itself.

## Harness capability discovery

Before assuming this bundled `urllib`-based script is the only option,
check whether the host harness already exposes an HTTP-capable tool and
prefer that. Degrade gracefully to this script if discovery isn't
supported — never hard-fail because discovery itself isn't possible.

## CLI usage

```
python erp_client.py list-envs
python erp_client.py --tag qa health
python erp_client.py --tag qa query "Purchase Invoice" --filters '[["status","=","Overdue"]]' --fields '["name","supplier","outstanding_amount"]'
python erp_client.py --tag qa report "Accounts Receivable" --filters '{"company":"Acme"}'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Journal Entry" create --payload '{"...": "..."}'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Journal Entry" submit --name "ACC-JV-2026-00001"
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Journal Entry" cancel --name "ACC-JV-2026-00001"
```

## Extension point

To target a different ERP backend, replace `scripts/erp_client.py` and
this file (here and in `qkeee-erp-core`, the source of truth). Nothing in
`references/domain-knowledge.md` or this skill's `SKILL.md` needs to
change — they're written to be ERP-agnostic in substance.

## Audit-trail retrofit

`mutate_resource()` wraps every write with a two-phase log to the
`Qkeee Bot Audit Log` doctype (`Attempted` before the real call,
`Success`/`Failure` after), best-effort throughout — a target instance
that hasn't run `qkeee-erp-bot-init` yet keeps writing exactly as before
this retrofit, just unaudited. `query_resource()`/`get_resource()`/
`run_query_report()` gained an opt-in `debug` kwarg (`qkeee_erp.debug`)
for `Read`-row logging, off by default. `AUDIT_EXEMPT_DOCTYPES` prevents
the logger from recursively logging itself or double-logging the audit
Comment write. Full mechanism and doctype schema:
`qkeee-erp-core/references/connector-reference.md`'s own "Audit-trail
retrofit" section and `qkeee-erp-bot-init/references/bot-doctypes-
design.md`.
