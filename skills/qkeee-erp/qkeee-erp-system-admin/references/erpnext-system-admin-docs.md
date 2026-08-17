# ERPNext system-admin surface — docs map + live findings

Curated pointers into `docs.frappe.io` plus everything confirmed live
against `<erp-instance>`. Consult the
live findings first — several diverge from what generic ERPNext
community docs describe.

## Docs pointers

- `docs.frappe.io/frappe` (Frappe Framework, not ERPNext-specific) —
  User, Role, Role Permission Manager, Custom Field, Property Setter,
  Workflow, Data Import, Webhook are all core Frappe framework
  doctypes, not ERPNext ones. `docs.frappe.io/erpnext`'s "Setup" /
  "Users and Permissions" section links back to the same framework
  docs — there is no ERPNext-specific permission model layered on top.
- Role Permission Manager (desk page, not a doctype) —
  `docs.frappe.io/frappe/user/manual/en/setting-up/users-and-permissions/role-based-permissions`.
  Backed by `frappe.core.page.permission_manager.permission_manager`
  (see connector-reference.md's Endpoints table) — confirmed this is
  the only working REST read/write path for permission rows.

## Live findings (<erp-instance>)

**Instance:** ERPNext v15.110.0 / Frappe v15.110.0, apps `frappe`,
`erpnext`, `hrms`, `crm`. Same instance/version as prior `qkeee-erp-*`
builds this session — see the shared demo-instance memory note for
company/app details not repeated here.

**DocPerm is not independently queryable.** `GET
/api/resource/DocPerm?filters=[["parent","=","Purchase Order"]]` fails
live with `frappe.exceptions.PermissionError` even as Administrator —
the child-doctype-list permission check resolves against the wrong
parent context for this specific doctype over the REST v1 API. The
DocType's own record (`GET /api/resource/DocType/<name>`) DOES embed
its permission rows in a `permissions` array (read-only, useful for a
one-off inspection) — but the authoritative, mutation-aware read path
is `get_permissions(tag, doctype)` (`frappe.core.page.
permission_manager.permission_manager.get_permissions`), confirmed to
merge shipped `DocPerm` rows with `Custom DocPerm` overrides and
include a `linked_doctypes` array per row that the raw DocType meta
does not carry.

**Role Permission Manager write methods — full signatures confirmed
live** by probing each with an empty `{}` payload (Python's resulting
`TypeError` names the missing required positional args, same technique
used in prior `qkeee-erp-*` builds for whitelisted-method discovery):

- `add(parent, role, permlevel)` — creates a bare row, every right OFF.
- `update(doctype, role, permlevel, ptype, value=1, if_owner=0)` —
  flips one right.
- `remove(doctype, role, permlevel)` — deletes the row.
- `reset(doctype)` — wipes all custom overrides for the doctype
  (signature-confirmed only, not round-tripped — see connector-
  reference.md's Known gaps).

**Full add → verify → update → verify → remove → verify round trip
confirmed live**, both via direct Python calls and via the `erp_client.py`
CLI itself (`permission add/update/remove` subcommands): granted
`Auditor` read access to `Contact` at permlevel 0, flipped `write` to
1, then removed the row entirely — `get_permissions("Contact")`
reflected each step correctly, and the row was gone after `remove`.
The created override's `name` (e.g. `urbuiap86g`) uses a different
autoname pattern than the underlying shipped `DocPerm` rows (e.g.
`nio4h4t7ib`) — both are 10-character random IDs but were generated at
different times/by different doctypes (`Custom DocPerm` vs `DocPerm`),
confirming the override mechanism, not an in-place edit.

**User creation + role assignment confirmed live, full round trip.**
`POST /api/resource/User` with `{"email": "...", "first_name": "...",
"send_welcome_email": 0, "roles": [{"role": "Auditor"}]}` creates the
user AND attaches the role in the same call — the `roles` child table
accepts a bare `[{"role": "<name>"}]` list on create, no separate
Has Role creation step needed. Confirmed the created user is
disable-able (`PUT` `{"enabled": 0}`) and, since it was never
referenced by any other record, delete-able cleanly (`DELETE` returned
`{"data": "ok"}`) — also round-tripped through `destructive_mutate()`'s
CLI path directly, not just the raw API. A user who owns/created other
records is expected to fail delete with `LinkExistsError` instead (same
mechanism documented across every prior `qkeee-erp-*` build for
submitted/ledger-touching records) — `disable_user`, not `delete_user`,
is the practical path for a departed employee whose account has history.

**Custom Field create confirmed live — but with a real meta-cache-lag
trap.** `POST /api/resource/Custom Field` with `{"dt": "ToDo",
"fieldname": "...", "label": "...", "fieldtype": "Data", "insert_after":
"description"}` creates the field cleanly and it's immediately visible
via `GET /api/resource/Custom Field/<dt>-<fieldname>` (queried
directly). **But `GET /api/resource/DocType/<dt>` does NOT include the
new field in its `fields` array afterward** — DocType meta is cached
server-side and a REST-created Custom Field doesn't invalidate that
cache from this API's perspective. Tried `POST
/api/method/frappe.clear_cache` to force it — **not whitelisted, 403
even as Administrator.** There is no confirmed way to force the meta
cache to refresh over this REST API; the practical guidance is: verify
a Custom Field's creation by querying the `Custom Field` resource
directly by name, never by re-fetching `DocType/<dt>` meta. (A desk-UI
page load would presumably pick up the change via its own cache
invalidation path — not tested, out of scope for a REST-only
connector.)

**Property Setter schema confirmed live** (`doc_type`, `field_name`,
`property`, `property_type`, `value`) — not round-tripped
create→verify→delete this build (judged lower-priority than Custom
Field given time budget; same meta-cache-lag caveat almost certainly
applies, noted as an assumption in `render_customization_draft.py`'s
guidance, not independently confirmed for this specific doctype).

**Workflow schema confirmed live** (`workflow_name`, `document_type`,
`is_active`, `send_email_alert`, `states` child table → `Workflow
Document State`, `transitions` child table → `Workflow Transition`,
`workflow_state_field`). No Workflow records exist on this instance
(`query` returned an empty list) — not round-tripped create/activate
this build; this skill supports a plain `is_active` toggle on an
*existing* Workflow via `mutate_resource()` only, nothing more.

**Data Import schema confirmed live** (`reference_doctype`,
`import_type` — Insert New Records / Update Existing Records,
`import_file` an Attach field, `status` — Pending/Success/Partial
Success/Error/Timed Out). No records exist on this instance. Actual
import execution requires a binary file upload this connector has no
primitive for — guidance-only capability, see SKILL.md.

**Webhook schema confirmed live** (`webhook_doctype`, `webhook_docevent`
— after_insert/on_update/on_submit/on_cancel/on_trash/
on_update_after_submit/on_change, `enabled`, `request_url`,
`request_method` — POST/PUT/DELETE, `webhook_secret` a Password field).
No records exist on this instance. Not round-tripped this build; schema
grounds `mutate_resource(..., "create", ...)` for the "creating a new
Webhook" write path in SKILL.md.

**Notification records exist and are queryable read-only** — 5+
pre-existing Notification records confirmed live (e.g. "Exit Interview
Scheduled", "Material Request Receipt Notification"), all `channel:
"Email"`, `enabled: 1`.

**System health signals:**

- `frappe.utils.scheduler.get_scheduler_status` confirmed live,
  returns `{"status": "active"}` (also `"inactive"`/`"paused"` per
  Frappe framework docs, not observed on this instance).
- `Scheduled Job Type` is queryable and populated with real
  `last_execution` timestamps (e.g. `accounts_controller.
  update_invoice_status` last ran within the hour at build time) —
  useful for spotting a job that's stopped running.
- `Error Log` is queryable and had real recent entries at build time
  (e.g. a `get_items()` missing-argument error, a `make_opportunity()`
  missing-argument error) — useful as a raw health signal even without
  deep investigation of each entry's cause.
- **`RQ Job` is NOT usable via this REST API** — `GET
  /api/resource/RQ Job` 500s with `TypeError: argument of type
  'NoneType' is not iterable`, unrelated to auth/permissions (same
  error for Administrator as for anyone). No confirmed alternative for
  reading live background-job-queue depth over REST; report this as an
  explicit gap in any health report rather than omitting queue depth
  silently.
- `GET /api/resource/Log Settings` (a Single doctype) fails with a
  `pymysql.err.ProgrammingError` when queried as a list resource — the
  Single-doctype fetch pattern differs from a normal list-backed
  doctype and wasn't pursued further (System Settings/Log Settings
  review is not in this skill's built capability set).

## Not live-tested (schema-grounded only)

- `reset` permission-manager action (signature only — see above).
- Property Setter create/delete round trip.
- Workflow create/activate/transition round trip.
- Data Import execution (no file-upload primitive).
- Webhook create/trigger round trip.

## Live validation record

`<erp-instance>`, temporary API key/secret (generated via
session login + `frappe.core.doctype.user.user.generate_keys`, revoked
immediately after via `PUT /api/resource/User/Administrator` with
`{"api_key": null}`, reconfirmed the old token 401s afterward):

- `health` / `scheduler-status` / `roles-and-doctypes` / `get-permissions`
  / `query` (Scheduled Job Type, Error Log, Role, User, Notification,
  Custom Field, Property Setter, Custom DocPerm, Data Import, Webhook)
  all confirmed via the CLI.
- Permission add → get_permissions → update → get_permissions →
  remove → get_permissions full round trip, via CLI `permission`
  subcommand with real computed `confirmation_token`s.
- User create (with role) → fetch → disable → delete, the delete step
  via CLI `destructive-mutate` with a real computed
  `confirmation_token`.
- Custom Field create → verify via direct `Custom Field` query → (meta
  cache lag confirmed) → delete (cleanup).
- No test fixtures left behind — every created record (test user,
  Contact permission override, ToDo custom field) was cleaned up
  within this build session.
