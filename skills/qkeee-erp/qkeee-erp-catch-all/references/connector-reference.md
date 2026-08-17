# qkeee-erp connector reference (`qkeee-erp-catch-all`'s synced copy)

This is `qkeee-erp-catch-all`'s copy of the canonical connector reference,
synced from `qkeee-erp-core/references/connector-reference.md` per the
module plan's self-contained-copies decision — `qkeee-erp-core` remains
the source of truth; do not hand-diverge this copy. This skill also ships
`scripts/discover.py` on top (app/module/doctype resolution — not part of
the canonical connector, specific to catch-all's investigation workflow;
see `references/domain-knowledge.md`).

## What this layer does, and doesn't, know

- **Does know:** auth, environment/tag resolution, generic REST primitives,
  the read-only/read-write gate.
- **Doesn't know:** any domain judgment (what counts as a valid GST return,
  what a 3-way match should check, whether an offer letter needs a second
  approval). That belongs in each persona skill's `domain-knowledge.md`.

If `qkeee-erp` ever needs to target a different ERP backend, this file and
`erp_client.py` are what change — domain-knowledge.md and persona
instructions do not.

## Auth

ERPNext (Frappe framework) REST API, token auth:

```
Authorization: token <api_key>:<api_secret>
```

Keys are generated per ERPNext user via **User → API Access → Generate
Keys** in the ERPNext UI (org's ERPNext admin provisions these) — this is a
manual, org-side onboarding step, not something this skill automates.

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
second/third environment later, sets that tag's three vars in their shell
themselves at runtime (the skill walks them through naming and var-setting,
it doesn't declare the vars for them):

| Variable | Purpose |
| --- | --- |
| `QKEEE_ERP_<TAG>_BASE_URL` | e.g. `https://org.erpnext.com` |
| `QKEEE_ERP_<TAG>_API_KEY` | API key for that site/user |
| `QKEEE_ERP_<TAG>_API_SECRET` | API secret for that site/user |

`<TAG>` is uppercased/sanitized from whatever the user names it (`qa`,
`client-a-prod`, etc). Adding a second/third environment is a runtime
action — walk the user through naming a new tag and setting its three vars,
then offer to switch `qkeee_erp.active_env`. Never store these values in
`metadata.hermes.config` or in agent-curated memory (`MEMORY.md`) — only
the **tag name** (not URL/credentials) may go there, per the
active-environment-reminder convention.

Missing-var failures must name the exact variable
(`QKEEE_ERP_QA_API_KEY`), never a generic "auth failed."

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
`_request()` now sends an explicit `User-Agent: qkeee-erp-core/1.0` on
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
id/email of the human who asked for the change, sourced from
`metadata.hermes.config` → `qkeee_erp.requested_by`. Missing it raises
`MissingRequesterError`, same enforcement style as the read-only gate
(checked in code, immediately before the HTTP call).

On a successful create/update/submit/cancel/delete, `mutate_resource()`
calls `record_comment(cfg, doctype, name, content)`, which POSTs to
`frappe.desk.form.utils.add_comment`:

```
{"reference_doctype": "<DocType>", "reference_name": "<name>", "content": "..."}
```

Comment content follows the fixed shape `[<SKILL_LABEL>] <action> —
requested by <requested_by>, applied via qkeee-erp bot.` `SKILL_LABEL` is a
module-level constant in `erp_client.py` — set to `"qkeee-erp-core"` here,
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

## Save-draft-then-review-then-submit discipline

`mutate_resource()`'s `create`/`update`/`submit` actions were already
independent, separately-callable actions before this note — `create`/
`update` never implicitly submits a record (ERPNext itself leaves a newly
created or updated record at `docstatus 0` unless `submit` is called
separately). This is a **skill-instruction discipline**, not a connector
change: every persona skill's Execute-stage instructions for a
create/update capability must now sequence as:

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

## Query pagination

`query_resource()` requests `limit + 1` rows and trims back to `limit`,
returning `{"data": [...], "has_more": bool, "limit": N}`. A caller that
ignores `has_more` and treats a truncated result as complete (e.g. an
aging report that silently drops rows past 20) is a bug in the calling
skill, not something the connector can prevent by itself — surface
`has_more` to the user or re-query with a higher `--limit`/tighter filters.

## CLI usage (for manual/ad hoc use, or reference by persona skills)

`--tag` is required for `health`/`query`/`get`/`mutate`; `--mode` and
`--requested-by` are required for `mutate` only. None fall back to an
ambient shell variable — all must be passed explicitly by the caller
(sourced from `qkeee_erp.active_env` / `qkeee_erp.mode` /
`qkeee_erp.requested_by`), so a stray env var left in someone's shell
profile can never silently pick the mode or spoof a requester.

```
python erp_client.py list-envs
python erp_client.py --tag qa health
python erp_client.py --tag qa query "Purchase Order" --filters '[["status","=","To Bill"]]' --fields '["name","status","supplier"]'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Journal Entry" submit --name "JE-0001"
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Journal Entry" create --payload '{"...": "..."}'
```

## Audit-trail retrofit

`mutate_resource()` wraps every write with a two-phase log to the
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
gated on `qkeee_erp.debug` at the SKILL.md level) and thread the returned
`session_id` through subsequent `mutate`/`query`/`get` calls. `open_session()`
returns a locally-generated fallback id (`local-<timestamp>`) if the insert
itself failed, so callers always have a usable `session_id` string to pass
along even when Session logging isn't actually landing anywhere —
`Qkeee Bot Audit Log.session` is a plain Data field precisely so it can
carry either a real Session row's `name` or this fallback string
interchangeably (see bot-doctypes-design.md decision 10).

The retrofit above was synced
into all 7 write-capable persona skills' own `erp_client.py` copies plus
`qkeee-erp-mis-analyst`
(`qkeee-erp-catch-all`'s own copy — see `scripts/erp_client.py` in this
skill — already carries it). Two narrow gaps remain, both because the
bypassed function never calls `mutate_resource()`: fixed-asset-manager's
`call_whitelisted_method()` (depreciation/scrap/restore/disposal) and
system-admin's `call_permission_manager()` (permission add/update/remove/
reset) still enforce the double-confirm token gate and `requested_by` in
full, but don't produce a `Qkeee Bot Audit Log` row. See the module plan's
"Bot Audit-Trail Doctype Design" section for the full sync/gap log.
