# qkeee-erp-inventory connector reference

This skill's copy of the `qkeee-erp` connector layer, synced from the
canonical version in `qkeee-erp-frappe-core/references/connector-reference.md`.
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
Keys** in the ERPNext UI (org's ERPNext admin provisions these). This can
be done manually as an org-side onboarding step, or automated
via `qkeee-erp-bot-init/scripts/ensure_bot_user.py`, which detects
whether the dedicated bot user already exists and, if not, creates it
(with the `Qkeee Bot` role attached) and generates its API key/secret —
using an elevated admin credential distinct from this key, same
dry-run/confirm discipline as that skill's doctype provisioning. Prefer
suggesting that path over manual UI steps when the user doesn't already
have a bot user configured.

**Must be a dedicated bot/integration user, not a human's login.** All
`qkeee-erp-*` skills share one ERPNext identity for reads/writes. Generate
this key against a dedicated integration/bot user (e.g.
`qkeee-erp-bot@<org>`) — never against an individual staff member's
personal account. If provisioned under a real person, every write in
ERPNext attributes to that person regardless of who actually requested it
in chat, silently defeating the requester-attribution mechanism below.

## Environment / tag model

Config is tagged, not a fixed dev/test/qa/prod enum. At install time the
frontmatter declares exactly one literal tag, `DEFAULT`
(`QKEEE_ERP_DEFAULT_BASE_URL`/`_API_KEY`/`_API_SECRET`) — `required_environment_variables`
can only declare static names, so it can't pre-declare a tag the user
hasn't chosen yet. A user who wants a different first tag name, or a
second/third environment later, sets that tag's three vars themselves at
runtime (the skill walks them through naming and var-setting, it doesn't
declare the vars for them):

| Variable | Purpose |
| --- | --- |
| `QKEEE_ERP_<TAG>_BASE_URL` | e.g. `https://org.erpnext.com` |
| `QKEEE_ERP_<TAG>_API_KEY` | API key for that site/user |
| `QKEEE_ERP_<TAG>_API_SECRET` | API secret for that site/user |
| `QKEEE_ERP_<TAG>_DEBUG` | OPTIONAL, default `false`. Per-tag debug logging — see "Requester attribution and debug are per-tag" below |
| `QKEEE_ERP_<TAG>_REQUESTED_BY` | OPTIONAL, no default. Per-tag requester identity — same section below |

`<TAG>` is uppercased/sanitized from whatever the user names it (`qa`,
`client-a-prod`, etc). Adding a second/third environment is a runtime
action — walk the user through naming a new tag and setting its three vars,
then offer to switch `qkeee_erp.active_env`. Never store these values in
`metadata.hermes.config` or in agent-curated memory (`MEMORY.md`) — only
the **tag name** (not URL/credentials) may go there, per the
active-environment-reminder convention.

**Where these vars live — this agent profile's own `.env`, never a shared
one.** Hermes resolves `os.environ` from the active agent profile's own
`.env` file (`.hermes/profile/<profile-name>/.env`), not a repo-root or
cross-profile `.hermes/.env`. When walking a user through setting a tag's
three vars, always name that path explicitly (substituting the real
profile name) rather than a bare "set this in your shell" — a var written
to the wrong `.env` silently doesn't exist from this skill's point of view
(`get_env_config()` only sees what the active profile's process actually
inherited), which surfaces as a confusing "missing variable" error with no
obvious cause. One profile folder can target multiple ERPNext
environments at once: every tag's three vars can coexist as separate lines
in that same profile `.env` (`QKEEE_ERP_QA_*` alongside
`QKEEE_ERP_PROD_*`), with `qkeee_erp.active_env` selecting which tag is
live for this session — adding a tag means appending three more lines to
the existing file, never a separate file or a separate profile.

Missing-var failures must name the exact variable
(`QKEEE_ERP_QA_API_KEY`), never a generic "auth failed."

## Endpoints used

| Purpose | Method | Path |
| --- | --- | --- |
| Health check | GET | `/api/method/frappe.auth.get_logged_user` |
| Query a DocType (list, no child tables) | GET | `/api/resource/<DocType>?filters=...&fields=...&limit_page_length=...` |
| Single-resource full-doc fetch (incl. child tables) | GET | `/api/resource/<DocType>/<name>` |
| Introspect a DocType's field schema (build-time) | GET | `/api/resource/DocType/<DocType Name>` |
| Create | POST | `/api/resource/<DocType>` |
| Update | PUT | `/api/resource/<DocType>/<name>` |
| Submit (step 1) | GET | `/api/resource/<DocType>/<name>` |
| Submit (step 2) | POST | `/api/method/frappe.client.submit` |
| Cancel | POST | `/api/method/frappe.client.cancel` |
| Delete | DELETE | `/api/resource/<DocType>/<name>` |
| Run a built-in report | GET | `/api/method/frappe.desk.query_report.run?report_name=...&filters=...` |
| Fetch a user's roles | GET | `/api/resource/User/<name>` |

**Submit is two calls, not one.** `frappe.client.submit` builds its doc via
`frappe.get_doc(<payload>)`, not a DB load — a `{doctype, name}`-only
payload has no other field values, so ERPNext's mandatory-field validation
on `doc.submit()` fails. `mutate_resource(..., "submit", ...)` therefore
GETs the full record first, then POSTs that full doc to
`frappe.client.submit`. `cancel` doesn't have this problem —
`frappe.client.cancel(doctype, name)` looks the record up server-side.
**This means submit necessarily reposts every stored field verbatim,
including any PII already on the record** (flagged during
`qkeee-erp-hr-associate`'s adversarial review) — expected,
since submit locks in the record as-is rather than granting new write
access; a calling skill's PII-scope discipline governs what it writes
new values to via create/update, not what submit echoes back.

**Response shape is inconsistent across actions — confirmed live
during `qkeee-erp-accounts-executive`'s Journal Entry
create → submit → cancel round trip.** `create`/`update`/the GET before
submit all return `{"data": {...doc...}}`. `frappe.client.submit` and
`frappe.client.cancel` (both whitelisted RPC-style methods, not REST
resource calls) return `{"message": {...doc...}}` instead — a caller
that reads `result["data"]` unconditionally after any mutate action will
`KeyError` specifically on submit/cancel responses. `mutate_resource()`
itself doesn't normalize this (it returns whatever `_request()` got back
verbatim); any code built on top of it must branch on the action to know
which key holds the doc, or check for either key defensively.

`filters` is a JSON-encoded list of `[fieldname, operator, value]` triples;
`fields` is a JSON-encoded list of fieldnames. See
`docs.frappe.io/framework` for full REST query syntax and
`docs.frappe.io/erpnext` per-module pages for DocType field names — this
reference does not duplicate ERPNext's own field-level docs; consult them
at persona-skill build time for exact doctype/field lists.

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

`GET /api/resource/DocType/<DocType Name>` (e.g.
`/api/resource/DocType/Supplier`) returns that DocType's own definition,
including its `fields` array — fieldname, fieldtype, `reqd` (mandatory
flag), and `options` (link target / select choices) for every field on
the live instance. This is the authoritative way to confirm field
lists/mandatory flags for any persona skill's `domain-knowledge.md` or
this skill's own field-mapping references, instead of relying on
`docs.frappe.io` (which documents the general shape but not a specific
org's customizations) — requires System Manager–level read access to the
DocType doctype. Child-table doctypes (e.g. `Purchase Invoice Item`) are
queried the same way.

## The read-only/read-write gate

`mutate_resource()` in `erp_client.py` takes `mode` as an explicit
parameter (sourced from `metadata.hermes.config` → `qkeee_erp.mode`) and
refuses any create/update/submit/cancel/delete unless `mode ==
"read-write"`. This check happens in code, immediately before the HTTP
call — never rely on the calling skill's prompt alone to withhold the
write.

Persona skills that hardcode a stricter posture (e.g.
`qkeee-erp-mis-analyst`, always read-only regardless of the config value)
should simply never call `mutate_resource()` — their copy of this script
can omit the write path entirely if desired.

## Requester attribution and the audit-comment trail

Because every write authenticates as the shared bot identity above,
`mutate_resource()` also requires `requested_by` — the ERPNext user
id/email of the human who asked for the change, sourced per-tag from
`QKEEE_ERP_<TAG>_REQUESTED_BY` (see "Requester attribution and debug are
per-tag" above), with a CLI `--requested-by` as a per-call override.
Missing it raises `MissingRequesterError`, same enforcement style as the
read-only gate (checked in code, immediately before the HTTP call).

On a successful create/update/submit/cancel/delete, `mutate_resource()`
calls `record_comment(cfg, doctype, name, content)`, which POSTs to
`frappe.desk.form.utils.add_comment`:

```
{"reference_doctype": "<DocType>", "reference_name": "<name>", "content": "..."}
```

Comment content follows the fixed shape `[<SKILL_LABEL>] <action> —
requested by <requested_by>, applied via qkeee-erp bot.` `SKILL_LABEL` is a
module-level constant in `erp_client.py` — set to `"qkeee-erp-frappe-core"` here,
and to the persona skill's own name in every synced copy, so the comment
identifies which skill acted. `record_comment()` is best-effort: a comment
failure (e.g. a role lacking comment permission) is swallowed and never
blocks or rolls back the write it documents — returns `True`/`False`
rather than raising. `delete` posts the comment *before* issuing the
`DELETE` call, since there's no record left to attach a Comment to
afterward.

This generalizes the narrower pattern `qkeee-erp-system-admin` originally
used only for destructive-action reasons (`_record_reason_comment`,
scoped to delete/disable with a free-text reason) — that mechanism now
folds into this generic one; system-admin's reason text, where supplied,
gets appended to the standard requester-attribution comment rather than
posted separately.

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

`query_resource()` requests `limit + 1` rows and trims back to `limit`,
returning `{"data": [...], "has_more": bool, "limit": N}`. A caller that
ignores `has_more` and treats a truncated result as complete (e.g. an
aging report that silently drops rows past 20) is a bug in the calling
skill, not something the connector can prevent by itself — surface
`has_more` to the user or re-query with a higher `--limit`/tighter filters.

## Harness capability discovery

Before assuming this bundled `urllib`-based script is the only option,
persona skills should check whether the host harness already exposes an
HTTP-capable tool and prefer that. `discover_harness_http_tool()` is a
stub for this — it always reports nothing pre-discovered from inside a
plain Python script, since a script can't introspect the harness's own
tool registry. The actual discovery attempt (if the harness exposes a
skills/tools listing) happens at the SKILL.md/agent-instruction level, not
inside this file. Degrade gracefully to this script if discovery isn't
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
this file (here and in `qkeee-erp-frappe-core`, the source of truth). Nothing
in `references/domain-knowledge.md` or this skill's `SKILL.md` needs to
change — they're written to be ERP-agnostic in substance.

## Audit-trail retrofit

`mutate_resource()` now wraps every write with a two-phase log to the
`Qkeee Bot Audit Log` doctype (schema owned by the sibling skill
`qkeee-erp-bot-init`, see its `references/bot-doctypes-design.md` for the
full field list, permission matrix, and decision log — this section only
covers what changed in this connector):

1. **Before** the real HTTP call: `record_audit_log_start()` inserts a row
   with `status: "Attempted"`, `payload_before` (for `update`, fetched via
   an extra `GET` — `create` has nothing to diff against, so no pre-image
   fetch happens for it), and `user_approved` set from the caller's
   `user_approved` kwarg (`"Approved"` or `"Not Confirmed"` — never
   inferred, always explicit).
2. **After**: `record_audit_log_finish()` updates the same row to
   `"Success"` (with `payload_after` and a computed `field_diff` for
   `update`) or `"Failure"` (with `error_detail`), then best-effort
   `submit`s it to lock the row.

Both steps are **best-effort** — implemented as raw `_audit_insert()`/
`_audit_update()`/`_audit_submit()` helpers that swallow `ConnectorError`
and return `None`/`False` rather than raising. If `qkeee-erp-bot-init`
hasn't been run against the target instance yet, every mutate call still
works exactly as before this retrofit; it's simply unaudited until init
runs. This mirrors `record_comment()`'s existing best-effort posture,
applied to a bigger piece of infrastructure for the same reason: a user's
actual requested write should never fail because internal bookkeeping
infra isn't provisioned yet.

**`AUDIT_EXEMPT_DOCTYPES`** (`Qkeee Bot Session`/`Message`/`Audit Log`/
`Persona`, plus `Comment`) is checked before any audit-wrap step —
without it, logging a write to Audit Log would recursively log itself
forever, and every audited write would silently double-log itself via the
`Comment` write `record_comment()` already makes.

**Read logging is opt-in, not automatic.** `query_resource()`/
`get_resource()` take a `debug` kwarg (CLI `--debug`); only when true is a
single-shot (no two-phase — nothing to recover from mid-read) `"Success"`
row logged with `action: "Read"`. Left off by default because a
read-heavy caller (query-report-driven skills especially) could otherwise
generate far more Read rows than any write path, making Audit Log itself
the volume/bloat problem the debug gate exists to prevent.

**Session/Message are fully opt-in, per caller, via `open_session()`/
`log_message()`/`close_session()`.** None of these are called from inside
`mutate_resource()`/`query_resource()` automatically — a persona skill
adopting full conversation logging must call them explicitly (typically
gated on the active tag's `QKEEE_ERP_<TAG>_DEBUG` at the SKILL.md level)
and thread the returned
`session_id` through subsequent `mutate`/`query`/`get` calls. `open_session()`
returns a locally-generated fallback id (`local-<timestamp>`) if the insert
itself failed, so callers always have a usable `session_id` string to pass
along even when Session logging isn't actually landing anywhere —
`Qkeee Bot Audit Log.session` is a plain Data field precisely so it can
carry either a real Session row's `name` or this fallback string
interchangeably (see bot-doctypes-design.md decision 10).

**Not yet done — a known gap, not an oversight:** none of the 7
write-capable persona skills' own `erp_client.py` copies have been synced
with this retrofit yet. Each one still runs the connector version
predating the audit-trail retrofit (read-only-gate + requester-attribution
+ save-draft-review-submit, but no audit logging). Syncing this file into
each persona skill's `scripts/`
directory is the next mechanical step before any persona skill's writes
actually reach `Qkeee Bot Audit Log`.

## What this layer does, and doesn't, know

- **Does know:** auth, environment/tag resolution, generic REST primitives,
  the read-only/read-write gate.
- **Doesn't know:** any domain judgment (what counts as a valid GST return,
  what a 3-way match should check, whether an offer letter needs a second
  approval). That belongs in each persona skill's `domain-knowledge.md`.

If `qkeee-erp` ever needs to target a different ERP backend, this file and
`erp_client.py` are what change — domain-knowledge.md and persona
instructions do not.

## Verified against a live instance

Checked against `<erp-instance>`: **ERPNext v15.112.0 / Frappe
v15.112.0**, apps installed: `frappe`, `erpnext`, `hrms` (Frappe HR
15.61.0), `crm` (1.57.9). **No India Compliance app installed** — build
time for `qkeee-erp-accounts-executive` should confirm whether the target
org's instance has it before assuming GSTIN/e-invoicing/e-way-bill
capabilities have dedicated fields to work against; on an instance without
it, `tax_id` is a generic Data field, not a GST-specific one.

**Full end-to-end round-trip validated** using a temporary
API key/secret (generated for the test, revoked immediately after):
`erp_client.py list-envs` / `health` / `query` (incl. `filters` and
`has_more` pagination) / `mutate create` / `mutate submit` / `mutate
cancel` all confirmed working against live data — including the
fetch-then-submit two-step fix (created a balanced Journal Entry,
submitted it via the two-call path, confirmed `docstatus: 1`, then
cancelled it, confirmed `docstatus: 2`). The read-only gate was also
confirmed to refuse a `create` call with a specific error when `--mode
read-only`.

**Found and fixed during this validation: Python's default urllib User-
Agent (`Python-urllib/3.11`) got blocked with a 403 by this instance's
WAF/bot-protection (Cloudflare) — a `curl` request with the same token
auth succeeded immediately.** The 403 body looked identical in shape to
an ERPNext auth failure, which would have been actively misleading.
`_request()` now sends an explicit `User-Agent: qkeee-erp-frappe-core/1.0` on
every call. Any org fronting their ERPNext instance with a WAF/CDN is a
plausible deployment, not a demo-only quirk — keep this header set in
every persona skill's connector copy.

**`delete` after `cancel` isn't reliably usable for ledger-touching
doctypes** (Journal Entry, Payment Entry, and similar): even after
`cancel` succeeds (`docstatus: 2`), ERPNext often still has a linked GL
Entry (or similar) referencing the document, and `DELETE
/api/resource/<DocType>/<name>` fails with `LinkExistsError`. Confirmed
live: a cancelled test Journal Entry could not be deleted through the
REST API for this reason and was left in place, cancelled, clearly
labeled via `user_remark`. This isn't a bug in this connector — it's
ERPNext protecting referential integrity — but every persona skill whose
domain-knowledge describes an "undo" flow for a ledger-touching doctype
should describe **cancel**, not delete, as the practical undo, and should
expect `delete` to fail on anything that's ever been submitted.

Session-based login (`POST /api/method/login` with `usr`/`pwd`) and `GET
/api/method/frappe.auth.get_logged_user` (the health-check endpoint) were
also exercised directly with `curl` during this validation, ahead of the
scripted round-trip above. Session login and token auth hit
the same underlying auth middleware, so this doesn't change the endpoint
table, but worth a real token-based round-trip test before treating this
skill as fully field-validated end-to-end.

## List endpoint vs. single-resource GET — child tables and token cost

Confirmed live against `<erp-instance>` while investigating input-token cost
across the `qkeee-erp-*` skills:

- **The list endpoint (`query_resource()`) silently drops child-table
  (Table-field) data even when named in `fields`** — requesting
  `["name","items"]` on Sales Order returns `name` only, no error, no
  `items` key at all. Child-table rows are only ever returned by the
  single-resource GET.
- **The single-resource GET ignores `fields` entirely** — it always
  returns the full doc regardless of query params. A Sales Order full doc
  measured 8,378 bytes (compact JSON) — 94 top-level keys, 265 leaf
  fields, including presentation-only HTML fields
  (`other_charges_calculation`, `terms`) nobody's review logic reads.
- The list endpoint **with** `fields` is roughly **25x cheaper** than a
  full single-GET for data that doesn't need child tables (336 bytes vs
  8,378 bytes measured on an identical Sales Order status read).

`get_resource()` was added to `erp_client.py` (CLI: `erp_client.py get
<DocType> <name>`) for the cases that genuinely need child-table data
(Link-field validity review before a submit). It noise-strips
audit/system metadata and presentation-only HTML fields by default
(`_NOISE_FIELDS` in `erp_client.py`) — measured **~38% byte reduction**
on the same Sales Order doc, with zero Link fields or child-table rows
dropped. `--no-strip` returns the doc verbatim if a caller ever needs
every raw field.

**Rule of thumb for every persona skill's instructions:** if a read
doesn't need child-table rows (status checks, report reads, dashboard
figures), use `query_resource()`/`--filters --fields`. If it does
(reviewing line items, checking a child row's Link field before submit),
use `get_resource()`/`get`, not `query --filters` — `query` won't return
the data being checked, it'll silently omit it rather than error.

## Built-in reports vs. hand-aggregated queries

`run_query_report()` (CLI: `erp_client.py report "<report_name>" --filters
'{...}'`) runs one of ERPNext's own server-side Query/Script Reports via
`frappe.desk.query_report.run`, instead of a persona skill hand-aggregating
raw transactional rows into the same shape. Prefer it whenever a built-in
report covers the need — report logic already implements Finance Book
gates, Accounting Dimension filters, and currency conversion; reimplementing
one of those from a raw `query_resource()` call risks silently missing a
detail and producing a plausible-looking but wrong figure. **Confirmed
live** ("Sales Order Analysis" against a real ERPNext v15 instance, real
per-line delay/pending-amount data returned) using GET with `filters` as a
JSON-encoded query string param — not POST with a JSON body, which is
untested against a live instance and should not be assumed to work
identically. `filters` field names are report-specific and undocumented by
this generic endpoint; confirm them by opening the report in the ERPNext UI
once. Falls back to `query_resource()` + hand aggregation only for a
genuinely custom cut no built-in report covers.

## Authority checks without a configured Workflow

`get_user_roles()` (CLI: `erp_client.py roles [--user <id>]`) fetches a
user's assigned roles from `User.roles` — the standard heuristic for "does
this user plausibly hold authority for this write" when the target
doctype has no ERPNext Workflow doctype configured (common on a default-
configured instance; confirmed live for Purchase Order on at least one
reference instance — no Workflow existed, role membership was the only
signal available via REST). An **empty roles list is ambiguous** — it
could mean the user genuinely holds no relevant role, or that the lookup
silently came back thin (wrong username resolved, or this API key lacks
permission to read `User.roles`). `get_user_roles()` returns a non-empty
`warning` string in that case rather than letting a caller conflate
"checked, no authority" with "didn't resolve" — treat both as "authority
not confirmed" and corroborate with the user, never assume the former. An
org with a real approval Workflow configured should be asked about it
directly instead of relying on this heuristic.

## Confirmation tokens for double-confirm writes

`confirm_token.py` (new in core, not present before this sync) provides
two shared primitives — `compute_token(**fields)` (deterministic hash over
arbitrary facts) and `is_fresh(issued_at, max_age_seconds, now)` (rejects
stale or implausibly-future tokens, default 15-minute TTL) — used to tie a
render/stage step to its later execute step for any write a persona skill
gates behind a DOUBLE confirm (depreciation runs, disposals, destructive
sysadmin actions, bot-user provisioning). Capability-specific token
constructors stay in the owning persona skill's own `confirm_token.py`,
built on these two primitives — see the module's docstring for the
expected shape. **Every token constructor must include an `issued_at`
field and its execute step must call `is_fresh()` before honoring the
token** — a token with no freshness check never expires, which defeats the
anti-replay property the whole mechanism exists for. (Found during this
sync: one persona skill's confirmation tokens omitted this check entirely
— fixed as part of the same retrofit that added this file to core.)

## Runtime metadata discovery

`discover.py` (new in core, promoted from a persona skill during this
sync) resolves what's actually installed/configured on a target instance
— `list_installed_apps()`, `list_modules()`, `doctype_meta()`,
`resolve_doctype()` — instead of trusting `docs.frappe.io` or a GitHub
README, which describe the general shape of an app but not a specific
org's customizations (custom fields, altered mandatory flags, locally
added doctypes). `list_installed_apps()`'s underlying RPC
(`frappe.utils.change_log.get_versions`) is opportunistic, not guaranteed
— confirmed live to 403 with `PermissionError: ... is not whitelisted` on
at least one real instance; `list_modules()` (a plain REST read against
`Module Def`) is the confirmed-working primary app-discovery path, not a
fallback. `resolve_doctype()` distinguishes "no module recorded" (`app:
null, app_lookup_error: null`) from "the module lookup itself failed"
(`app: null, app_lookup_error: "<message>"`) — collapsing both to a bare
`app: null` risks a false "this is custom, no owning app" claim when a
lookup had actually just errored. Every persona skill should attempt this
discovery before proposing a field/doctype it hasn't confirmed live on the
target instance.

## Pitfalls found live, worth knowing before you hit them

(Connector-layer only — REST mechanics/quirks that apply regardless of
doctype. Doctype-specific business-logic pitfalls, e.g. Stock
Reconciliation's batch-tracking behavior, belong in the owning persona
skill's own `domain-knowledge.md`/connector-reference, not here — see
"What this layer does, and doesn't, know" above.)

- **`RQ Job` doctype is not usable via REST** — confirmed live 500
  `TypeError`, unrelated to permissions/auth. Use
  `frappe.utils.scheduler.get_scheduler_status` + `Scheduled Job Type` +
  `Error Log` for system-health signals instead; report the RQ Job gap
  explicitly in a health report rather than silently omitting queue depth.
- **A freshly created Custom Field does not appear in a subsequent `GET
  /api/resource/DocType/<dt>` meta fetch** — the meta cache isn't
  invalidated by a REST create, and `frappe.clear_cache` isn't whitelisted
  (403 even as Administrator). Verify a new Custom Field via a direct
  `Custom Field` resource query by name, never by re-fetching DocType meta.

## Save-draft-then-review-then-submit discipline

`mutate_resource()`'s `create`/`update`/`submit` actions are
independent, separately-callable actions — `create`/
`update` never implicitly submits a record (ERPNext itself leaves a newly
created or updated record at `docstatus 0` unless `submit` is called
separately). This is a **skill-instruction discipline**, not a connector
concern: every persona skill's Execute-stage instructions for a
create/update capability must sequence as:

1. **Save as draft** — `mutate_resource(..., "create"/"update", ...)`.
2. **Review the saved draft** — `get_resource()`/`erp_client.py get` by
   the returned `name` (re-fetch, don't reuse the outgoing payload) and
   check every field as actually persisted, specifically that every
   Link-type field (Supplier, Item, Employee, Account, Cost Center, etc.),
   including ones nested in a child table, resolves to a real, existing
   record. Use `get`, not `query --filters` — the list endpoint silently
   drops child-table data (see above), so it can't be used to validate
   Link fields inside line items. Fix via a further `update` and
   re-review if anything is wrong — never submit an unreviewed or
   known-bad draft.
3. **Submit** — `mutate_resource(..., "submit", ...)` as its own distinct
   call, only once review confirms the draft is correct.

Nothing in `erp_client.py` needed to change for this — the file already
supports steps 1 and 3 as separate calls. Step 2 is not a connector
function at all; it's the calling skill using the existing `query_resource`
(or a plain `GET`) against the record `create`/`update` just returned.

## CLI usage (for manual/ad hoc use, or reference by persona skills)

`--tag` is required for `health`/`query`/`get`/`mutate`; `--mode` is
required for `mutate` only. Neither falls back to an ambient value —
both must be passed explicitly by the caller (sourced from
`qkeee_erp.active_env` / `qkeee_erp.mode`), so a stray env var left in
someone's shell can never silently pick the mode. `--requested-by` and
`--debug` are different: they DO have a fallback, but a deliberate,
scoped one — the active tag's own `QKEEE_ERP_<TAG>_REQUESTED_BY` /
`_DEBUG` (see "Requester attribution and debug are per-tag" above), not
an arbitrary shell variable. `mutate`/`open-session` still refuse to run
if neither the env var nor the flag resolves to a requester.

```
python erp_client.py list-envs
python erp_client.py --tag qa health
python erp_client.py --tag qa query "Purchase Order" --filters '[["status","=","To Bill"]]' --fields '["name","status","supplier"]'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Journal Entry" submit --name "JE-0001"
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Journal Entry" create --payload '{"...": "..."}'
```

## Interim / scratch files

Any file this skill's tooling needs to write that isn't the final
deliverable handed to the user — a JSON payload assembled for
`render_report.py`/`render_*_draft.py`, a downloaded attachment staged for
extraction, any other scratch artifact — is written under `terminal.cwd`
(from `config.yaml`'s `terminal:` block; e.g.
`/work/storage/hermes/agent-profiles/<profile-name>/cwd`), never `/tmp` or
another ad hoc path. `/tmp` isn't guaranteed to be the same filesystem
`terminal.cwd` runs against, isn't scoped to this profile (a second profile
running concurrently could collide on file names), and isn't guaranteed to
persist for the life of the session — writing there is how a mid-task file
silently disappears or leaks across profiles. Clean up scratch files once
they're no longer needed for the current task; don't leave them littering
`terminal.cwd` across sessions.

## Requester attribution and debug are per-tag, not global

`QKEEE_ERP_<TAG>_DEBUG` / `QKEEE_ERP_<TAG>_REQUESTED_BY` used to be a
single global `metadata.hermes.config` value (`qkeee_erp.debug` /
`qkeee_erp.requested_by`) shared across every tag in a profile — one
toggle, no matter which environment was active. Moved to per-tag env
vars (2026-08-18 retrofit) specifically because that was wrong in
practice: a profile juggling `hrms-demo` and `prod` had no way to run
debug logging on `hrms-demo` without it also being on for `prod`, and no
way to attribute writes to a different requester per environment without
manually re-confirming on every switch.

Both are OPTIONAL, unlike `BASE_URL`/`API_KEY`/`API_SECRET` — neither
raises if absent:

- **`QKEEE_ERP_<TAG>_DEBUG`** (`get_env_config()`'s `debug_default` key)
  — parsed as a bool (`1`/`true`/`yes`/`on`, case-insensitive; anything
  else is `false`). Defaults `false` if unset. A `--debug` CLI flag is a
  per-call override that can only turn it *on* for that one call — there
  is no equivalent to force it *off* for a single call on a tag that has
  the env var set to `true`.
- **`QKEEE_ERP_<TAG>_REQUESTED_BY`** (`requested_by_default` key) — no
  default; empty string if unset. A `--requested-by` CLI flag overrides
  it per-call. `mutate`/`open-session` refuse to run (a specific
  `p.error` naming the exact env var, not a generic failure) if neither
  the env var nor the flag resolves to a non-empty value.

`erp_client.py`'s CLI resolves both once per invocation, right after
argument parsing, by calling `get_env_config(args.tag)` — cheap (pure
`os.environ` reads, no network call) even though the same function gets
called again inside whichever real command runs. A resolution failure
here (e.g. the tag's `BASE_URL`/`API_KEY`/`API_SECRET` are themselves
missing) is swallowed at this point — the real command below raises its
own specific error for that, no need to duplicate it.

`qkeee_erp.active_env` and `qkeee_erp.mode` stay as global
`metadata.hermes.config` values, deliberately NOT moved alongside
debug/requested_by — switching environments should never silently also
change write access, so `mode` needs to require its own explicit
confirmation independent of which tag is active.
