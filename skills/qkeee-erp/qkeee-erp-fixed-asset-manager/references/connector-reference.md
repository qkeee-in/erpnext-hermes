# qkeee-erp-fixed-asset-manager connector reference

This skill's copy of the `qkeee-erp` connector layer, synced from the
canonical version in `qkeee-erp-core/references/connector-reference.md`.
Carries the full read+write path (`mutate_resource`) — no
capability-specific addition to `erp_client.py` itself for this skill
(unlike `qkeee-erp-procurement`'s `get_user_roles()`); the
capability-specific gates for this skill live entirely in
`scripts/render_asset_draft.py`, `scripts/render_depreciation_run.py`,
`scripts/render_disposal.py`, and `scripts/render_movement_draft.py`.

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
| Create | POST | `/api/resource/<DocType>` |
| Update | PUT | `/api/resource/<DocType>/<name>` |
| Submit (step 1) | GET | `/api/resource/<DocType>/<name>` |
| Submit (step 2) | POST | `/api/method/frappe.client.submit` |
| Cancel | POST | `/api/method/frappe.client.cancel` |
| Delete | DELETE | `/api/resource/<DocType>/<name>` |
| Best-effort audit comment | POST | `/api/method/frappe.desk.form.utils.add_comment` — body `{"reference_doctype": "...", "reference_name": "...", "content": "..."}` |
| Post due depreciation for one schedule | POST | `/api/method/erpnext.assets.doctype.asset.depreciation.make_depreciation_entry` — body `{"asset_depr_schedule_name": "..."}` |
| Scrap an asset | POST | `/api/method/erpnext.assets.doctype.asset.depreciation.scrap_asset` — body `{"asset_name": "..."}` |
| Restore a scrapped asset | POST | `/api/method/erpnext.assets.doctype.asset.depreciation.restore_asset` — body `{"asset_name": "..."}` |
| Draft a Sales Invoice for disposal-by-sale | POST | `/api/method/erpnext.assets.doctype.asset.asset.make_sales_invoice` — body `{"asset": "...", "item_code": "...", "company": "..."}` |

The four whitelisted-method rows are NOT part of `mutate_resource()`'s
generic `create`/`update`/`submit`/`cancel`/`delete` action set. They go
through `erp_client.call_whitelisted_method(tag, method, body, mode,
confirmation_token=None, token_facts=None, requested_by=None)` — the
single call path for all four, which enforces `mode == "read-write"` AND
`requested_by` in code (identical gates to `mutate_resource()`, no
longer a call-site-only convention; missing `requested_by` raises
`MissingRequesterError`). For the three double-confirm methods
(`make_depreciation_entry`, `scrap_asset`, `make_sales_invoice`), it
additionally requires `confirmation_token` to match the token computed
from `token_facts` (see `scripts/confirm_token.py`) — the same token the
corresponding render script (`render_depreciation_run.py`/
`render_disposal.py`) printed. `body` is sent to ERPNext verbatim as
that method's real arguments; `token_facts` is verification-only and
never enters the API payload. `restore_asset` is mode-gated but not
token-gated (recovery, not a write-off). On success, posts the same
best-effort audit Comment shape as `mutate_resource()` onto
`body["asset_name"]` (the field every one of these four RPCs takes) —
`[qkeee-erp-fixed-asset-manager] <method> — requested by <requested_by>,
applied via qkeee-erp bot.`

**Health check confirms connectivity + auth only**, not query/write-time
permission on a specific DocType — report a later 403/PermissionError as
its own distinct failure mode.

**Submit is two calls, not one** — see `mutate_resource()`'s docstring
and `qkeee-erp-core`'s canonical reference for why. This also means
submit reposts every stored field verbatim, including any sensitive
fields already on the record — expected, not a scope leak; see
`qkeee-erp-core`'s reference for the full note (flagged during
`qkeee-erp-hr-associate`'s adversarial review).

**Response shape differs by action.** `create`/`update`/the GET before
submit return `{"data": {...doc...}}`. `submit`/`cancel` and the four
whitelisted methods above (all RPC-style, not REST resource calls)
return `{"message": {...}}` — confirmed live for submit/cancel/scrap
(scrap's response was actually just a `_server_messages` confirmation
string, no structured `message` payload; re-query the Asset afterward
to get its updated `status`/`journal_entry_for_scrap` rather than
parsing the scrap response itself).

## Live validation record

See `references/erpnext-assets-docs.md`'s "Live validation record"
section for the full capitalize -> submit -> transfer -> scrap /
depreciate -> repair chain confirmed against `<erp-instance>`.
Key numbers: Asset `ACC-ASS-2026-00001` (scrap path),
`ACC-ASS-2026-00002` (depreciation-run path, 6 periods posted in one
`make_depreciation_entry` call), Asset Repair `ACC-ASR-2026-00001`.

## Discovering a DocType's real field list (build-time technique)

`GET /api/resource/DocType/<DocType Name>` returns that DocType's live
field definitions (fieldname, fieldtype, `reqd`, `options`) — used
throughout this build for Asset, Asset Category, Asset Category
Account, Asset Finance Book, Asset Depreciation Schedule, Depreciation
Schedule, Asset Movement, Asset Movement Item, Asset Repair, Asset
Maintenance, Asset Maintenance Task, Asset Maintenance Log. Prefer this
over `docs.frappe.io` for confirming an org's actual field list/
mandatory flags — this build found the same lesson every prior
`qkeee-erp-*` build has found: the declared `reqd` flag isn't always
the whole story (here: `asset_category` isn't schema-mandatory but is
practically required for depreciation to have anywhere to post).

**Discovering a whitelisted RPC method's exact signature (build-time
technique):** calling a whitelisted method with an
empty `{}` payload returns a Python `TypeError` naming the missing
required positional argument(s) — used to confirm
`scrap_asset(asset_name)`, `restore_asset(asset_name)`,
`make_depreciation_entry(asset_depr_schedule_name, date=None)`, and
`make_sales_invoice(asset, item_code, company)`'s exact argument names
without needing source access. A `ValidationError: Failed to get method
for command ...` response (rather than a `TypeError`) means the method
path itself is wrong for this version — don't confuse the two failure
modes when probing for a method's real location.

## The read-only/read-write gate

`mutate_resource()` takes `mode` as an explicit parameter (sourced from
`metadata.hermes.config` → `qkeee_erp.mode`) and refuses any
create/update/submit/cancel/delete unless `mode == "read-write"`. This
is the library-wide gate — identical to every other `qkeee-erp-*`
skill's copy. The four whitelisted-method calls above are NOT routed
through `mutate_resource()` (they don't fit its action set) — they go
through `call_whitelisted_method()` instead, which enforces the
identical `mode == "read-write"` check in code. Never call `_request()`
directly for these four methods; `_request()` itself has no mode
awareness and bypassing `call_whitelisted_method()` would silently
reintroduce the gap this function exists to close.

This skill's capability-specific, code-enforced gates are the
double-confirm renderers (`render_depreciation_run.py`,
`render_disposal.py`) and the completeness/integrity renderers
(`render_asset_draft.py`, `render_movement_draft.py`) — none of these
are the same thing as the mode gate above; all four sit closer to where
each draft/confirmation is built.

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

- **Submit is fetch-then-submit, not atomic** — same unmitigated gap
  documented in every prior `qkeee-erp-*` connector reference; a record
  edited by someone else between the GET and the POST could get
  submitted with a stale `full_doc`. Flag an unexpected
  `TimestampMismatchError` to the user rather than treating it as a
  generic write failure.
- **Sale-disposal path (`make_sales_invoice` through submission) not
  live-tested end to end** — see `erpnext-assets-docs.md`'s "Not
  live-tested" section.
- **Asset Maintenance capabilities not live-round-tripped** — schema
  confirmed live, create/submit flow not exercised at build time.
- **Revoking a session-derived API key via cookie-auth PUT may 403.** If
  a temporary API key/secret was minted via session login +
  `generate_keys`, revoking it (`api_key: null`) through the
  cookie-session PUT-with-CSRF path can return 403 on some instances;
  the token-auth `mutate_resource()` path (using the key being revoked)
  is the reliable fallback — confirm revocation succeeded by checking
  that the old token 401s afterward.

## Query pagination

`query_resource()` requests `limit + 1` rows and trims to `limit`,
returning `{"data": [...], "has_more": bool, "limit": N}`. Always check
`has_more` — an asset audit/verification pull across a large category
or location scope silently dropping rows past the default limit is a
bug in the calling report logic, not something the connector prevents
by itself.

## Harness capability discovery

Before assuming this bundled `urllib`-based script is the only option,
check whether the host harness already exposes an HTTP-capable tool and
prefer that. Degrade gracefully to this script if discovery isn't
supported — never hard-fail because discovery itself isn't possible.

## CLI usage

```
python erp_client.py list-envs
python erp_client.py --tag qa health
python erp_client.py --tag qa query "Asset" --filters '[["status","=","Submitted"]]' --fields '["name","status","location","asset_category"]'
python erp_client.py --tag qa query "Asset Depreciation Schedule" --filters '[["asset","=","ACC-ASS-2026-00002"]]'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Asset" create --payload '{"...": "..."}'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Asset" submit --name "ACC-ASS-2026-00002"
```

The four whitelisted-method calls (`make_depreciation_entry`,
`scrap_asset`, `restore_asset`, `make_sales_invoice`) are not exposed as
`erp_client.py` subcommands in this build — call
`erp_client.call_whitelisted_method()` directly from the invoking skill
logic with the exact body from the Endpoints table above; it resolves
the path, enforces the mode gate, and (for the three double-confirm
methods) the confirmation-token check.

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
this retrofit, just unaudited. `query_resource()`/`get_resource()`
gained an opt-in `debug` kwarg (`qkeee_erp.debug`) for `Read`-row
logging, off by default. `AUDIT_EXEMPT_DOCTYPES` prevents the logger
from recursively logging itself or double-logging the audit Comment
write.

**Known gap: `call_whitelisted_method()` bypasses this entirely.**
`make_depreciation_entry`/`scrap_asset`/`restore_asset`/
`make_sales_invoice` are RPC-style calls that don't fit `mutate_resource()`'s
create/update/submit/cancel/delete shape, so none of the four are
currently logged to `Qkeee Bot Audit Log` — they still get the usual
ERPNext Comment and the double-confirm token gate, just not this newer
audit row. Closing this means either a bespoke two-phase log call inside
`call_whitelisted_method()` or refactoring it onto a shared write-wrapper;
deferred as follow-up work, not fixed in this pass.

Full mechanism, decision log, and doctype schema:
`qkeee-erp-core/references/connector-reference.md`'s own "Audit-trail
retrofit" section and `qkeee-erp-bot-init/references/bot-doctypes-
design.md`.
