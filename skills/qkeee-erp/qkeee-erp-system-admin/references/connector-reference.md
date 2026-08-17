# qkeee-erp-system-admin connector reference

This skill's copy of the `qkeee-erp` connector layer, synced from the
canonical version in `qkeee-erp-core/references/connector-reference.md`.
Carries the full read+write path plus two skill-specific additions to
`erp_client.py`: `call_permission_manager()` (the Role Permission
Manager's own whitelisted methods) and `destructive_mutate()` (a
token-gated wrapper for the highest-blast-radius single-record
actions). Capability-specific gates otherwise live in the render
scripts (`render_user_draft.py`, `render_permission_change.py`,
`render_customization_draft.py`, `render_destructive_action.py`).

## Auth

ERPNext (Frappe framework) REST API, token auth:

```
Authorization: token <api_key>:<api_secret>
```

Keys are generated per ERPNext user via **User → API Access → Generate
Keys** — an org-side onboarding step, not automated here.

`get_env_config()` refuses a `base_url` that isn't `https://` — this
credential would otherwise go over the wire in plaintext — unless
`QKEEE_ERP_<TAG>_ALLOW_INSECURE=1` is explicitly set (a deliberate
local/dev opt-out, never the default path).

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
| List roles + doctypes (Role Permission Manager) | GET | `/api/method/frappe.core.page.permission_manager.permission_manager.get_roles_and_doctypes` |
| Get a doctype's full permission matrix | GET | `/api/method/frappe.core.page.permission_manager.permission_manager.get_permissions?doctype=<DocType>` |
| Add a bare permission row | POST | `/api/method/frappe.core.page.permission_manager.permission_manager.add` — body `{"parent": "<DocType>", "role": "...", "permlevel": 0}` |
| Update one right on an existing row | POST | `/api/method/frappe.core.page.permission_manager.permission_manager.update` — body `{"doctype": "...", "role": "...", "permlevel": 0, "ptype": "write", "value": 1, "if_owner": 0}` |
| Remove a permission row entirely | POST | `/api/method/frappe.core.page.permission_manager.permission_manager.remove` — body `{"doctype": "...", "role": "...", "permlevel": 0}` |
| Reset ALL custom overrides for a doctype | POST | `/api/method/frappe.core.page.permission_manager.permission_manager.reset` — body `{"doctype": "..."}` |
| System scheduler status | GET | `/api/method/frappe.utils.scheduler.get_scheduler_status` |
| Best-effort audit comment (every write path except permission changes) | POST | `/api/method/frappe.desk.form.utils.add_comment` — body `{"reference_doctype": "...", "reference_name": "...", "content": "..."}` |

**DocPerm is NOT independently queryable via `/api/resource/DocPerm`**
— confirmed live: filtering by `parent` fails with a `PermissionError`
even as Administrator (the child-doctype parent-permission check
resolves incorrectly for this table). The only confirmed working read
path for a doctype's permission matrix is
`get_permissions(tag, doctype)` (the same whitelisted method the Role
Permission Manager desk page itself calls) — it merges standard
`DocPerm` rows with any `Custom DocPerm` override rows and returns them
as one list, including a `linked_doctypes` array per row (what else
would be affected by removing that row) not present on the raw DocPerm
schema. `add`/`update`/`remove` write their result as `Custom DocPerm`
rows (confirmed live: a fresh row's `name` had a different ID pattern
than the underlying `DocPerm` rows) that merge into `get_permissions()`'s
output — they never edit the shipped `DocPerm` rows in place.

Four skill-specific wrappers, all token-gated:

- **`call_permission_manager(tag, action, doctype, role, permlevel,
  ptype=None, value=None, mode, confirmation_token=None, issued_at=None,
  requested_by=None)`** — the single call path for
  `add`/`update`/`remove`/`reset`. `reset` doesn't need `role`; the
  other three do (`ConnectorError` if omitted). All four require `mode
  == "read-write"`, `requested_by`, AND a `confirmation_token` +
  `issued_at` matching `confirm_token.permission_change_token(action,
  doctype, role, permlevel, ptype, value, issued_at)`, fresh within
  `DEFAULT_TOKEN_TTL_SECONDS` — computed by `render_permission_change.py`
  from the same facts. `requested_by` is enforced (`MissingRequesterError`
  if absent) but **no audit Comment is posted** for this wrapper — a
  DocPerm/Custom DocPerm row isn't a document instance with its own
  timeline, so there's no natural record to attach one to; surface
  `requested_by` in the calling skill's own report-back instead. Note
  `remove` only deletes the Custom DocPerm override row; a
  shipped/standard DocPerm row for the same doctype+role is untouched,
  so a right can survive `remove` if the standard row grants it
  independently — always re-check with `get_permissions()` after
  removing, don't assume it revoked. `get_roles_and_doctypes()`/
  `get_permissions()` are separate, never-gated read functions, not
  part of this wrapper.

- **`create_user(tag, email, first_name, roles, mode, send_welcome_email,
  elevated_confirmation_token=None, issued_at=None, requested_by=None)`**
  — wraps `mutate_resource(..., "create", ..., requested_by=requested_by)`
  for User, so the standard `[qkeee-erp-system-admin] created — requested
  by <requested_by>, applied via qkeee-erp bot.` Comment lands on the new
  User record same as any other create. If `roles` includes `System
  Manager`/`Administrator`, requires `elevated_confirmation_token` +
  `issued_at` matching `confirm_token.elevated_user_token(email, roles,
  issued_at)`, fresh within `DEFAULT_TOKEN_TTL_SECONDS` — the same
  code-level backstop permission changes and destructive actions get,
  since granting an elevated role is at least as high-privilege as
  either and was previously gated only by a boolean acknowledgment flag.
  Non-elevated role grants are unaffected (no token required).

- **`gated_config_mutate(tag, kind, doctype, identifier, reason, action,
  name=None, payload=None, mode="read-only", confirmation_token=None,
  issued_at=None, requested_by=None)`** — token-gated wrapper for
  `kind="create_webhook"` (`action="create"` on `Webhook`) and
  `kind="toggle_workflow"` (`action="update"` on `Workflow`) —
  previously plain single-confirm `mutate_resource()` calls with no
  code-level backstop. Requires a non-empty `reason`, `requested_by`,
  and a `confirmation_token` + `issued_at` matching
  `confirm_token.config_change_token(kind, doctype, identifier, reason,
  issued_at)`, fresh within `DEFAULT_TOKEN_TTL_SECONDS`. `identifier` is
  the webhook's `request_url` or the workflow's `document_type`. Calls
  `mutate_resource(..., skip_comment=True)` internally, then posts its
  own single Comment combining requester + reason via
  `_record_attribution_comment()`.
- **`destructive_mutate(tag, doctype, action, name, reason, mode,
  confirmation_token, issued_at, payload=None, requested_by=None)`** —
  wraps `mutate_resource()` for `action in ("update", "delete")`.
  `"update"` is accepted only for `doctype == "User"` (disable) — any
  other doctype+`"update"` combination is rejected outright rather than
  silently deriving a mismatched `action_key`. `"delete"` covers
  User/Custom Field/Property Setter/Webhook/Workflow. Requires a
  non-empty `reason`, `requested_by`, and a `confirmation_token` +
  `issued_at` matching `confirm_token.destructive_action_token(action_key,
  doctype, name, reason, issued_at)` — `issued_at` must be within
  `DEFAULT_TOKEN_TTL_SECONDS` (15 min) of now or the call is refused as
  stale, regardless of a matching token. `action_key` is `"disable_user"`
  for a User update or `"delete_<doctype_lowercased>"` otherwise —
  computed by `render_destructive_action.py` from the same facts. Calls
  `mutate_resource(..., skip_comment=True)` internally to avoid a
  duplicate plain comment, then best-effort writes one Comment combining
  `requested_by` + `reason` onto the affected record via
  `_record_attribution_comment()` (before the delete, for delete
  actions, since a deleted record can't be commented on afterward) — a
  comment failure never blocks or rolls back the action itself.

## Live validation record

See `references/erpnext-system-admin-docs.md`'s "Live validation
record" section for the full round trip confirmed against
`<erp-instance>`, 2026-08-11: permission add → get_permissions →
update → get_permissions → remove → get_permissions (all via the CLI
itself, not just the underlying Python functions); User create → fetch
→ disable → delete; Custom Field create → verify-via-direct-query →
delete, including the meta-cache-lag finding.

## Discovering a DocType's real field list (build-time technique)

`GET /api/resource/DocType/<DocType Name>` returns that DocType's live
field definitions — used for User, Role, Has Role, DocPerm, Custom
Field, Property Setter, Workflow, Data Import, Webhook, Scheduled Job
Type. **Caveat found this build:** this technique does NOT reliably
reflect customizations made via Custom Field/Property Setter — see the
meta-cache-lag finding in `erpnext-system-admin-docs.md`. For those two
doctypes specifically, query the `Custom Field`/`Property Setter`
resource directly instead of relying on `DocType/<dt>` meta.

## The read-only/read-write gate

`mutate_resource()` takes `mode` as an explicit parameter and refuses
any create/update/submit/cancel/delete unless `mode == "read-write"` —
the library-wide gate, identical to every other `qkeee-erp-*` skill's
copy. It also refuses every write missing `requested_by`
(`MissingRequesterError`) — the ERPNext user id/email of the human who
asked, sourced from `qkeee_erp.requested_by`, since the connector
authenticates as one shared bot identity. `call_permission_manager()`,
`create_user()`, `gated_config_mutate()`, and `destructive_mutate()`
enforce both checks independently in code (not by delegating silently)
before their own additional token checks — never call
`_request()`/the permission-manager whitelisted-method paths directly;
that would bypass the mode gate, the requester gate, and the token gate
this skill's non-negotiable depends on.

## Known gaps

- **The confirmation_token gate proves fact-consistency and recency, not
  human presence.** A matching token only proves the call is identical
  to what was rendered and that the render happened within
  `DEFAULT_TOKEN_TTL_SECONDS`. It cannot detect an agent that renders a
  confirmation and immediately consumes its own token in the same turn
  without a human actually replying — that discipline has to come from
  how this skill's instructions are followed, not from this connector.
  See `confirm_token.py`'s module docstring and SKILL.md's "Known
  limitation of the token gate."
- **The audit-trail Comment (`record_comment()` /
  `_record_attribution_comment()`) is best-effort and can silently
  fail.** If `add_comment` 403s/500s (e.g. a role without comment
  permission on that doctype), the write still proceeds — the only
  record of `requested_by`/`reason` in that case is the chat
  transcript. Not treated as fatal because blocking a legitimate write
  on a logging failure would be worse, but it means the audit trail
  isn't guaranteed. `call_permission_manager()` never even attempts one
  — see its entry above.
- **Submit is fetch-then-submit, not atomic** — same unmitigated gap
  documented in every prior `qkeee-erp-*` connector reference.
- **`reset` was signature-confirmed only, not round-tripped** — probing
  with an empty payload confirmed it takes only `doctype`, but an
  actual reset-and-verify-defaults-restored round trip was not run
  (the destructive nature of wiping every override for a doctype made
  this judged not worth doing against a shared demo instance beyond
  signature confirmation). Treat its exact behavior on a doctype with
  many existing overrides as unconfirmed.
- **Workflow states/transitions creation not live-tested** — schema
  confirmed live only; this skill supports a plain `is_active` toggle
  on an existing Workflow via `mutate_resource()`, nothing more.
- **RQ Job doctype is not usable via this REST API** — confirmed live
  500 (`TypeError`, unrelated to permissions) on a plain query. Live
  background-job-queue depth cannot be read this way; `get_scheduler_
  status()` + `Scheduled Job Type` + `Error Log` are the confirmed-
  working health signals instead.
- **Data Import execution not supported** — no file-upload primitive
  in this connector; reviewing existing Data Import record status is
  supported, driving an actual import is not.

## Query pagination

`query_resource()` requests `limit + 1` rows and trims to `limit`,
returning `{"data": [...], "has_more": bool, "limit": N}`. Always check
`has_more`.

## Harness capability discovery

Before assuming this bundled `urllib`-based script is the only option,
check whether the host harness already exposes an HTTP-capable tool and
prefer that. Degrade gracefully to this script if discovery isn't
supported.

## CLI usage

```
python erp_client.py list-envs
python erp_client.py --tag qa health
python erp_client.py --tag qa scheduler-status
python erp_client.py --tag qa roles-and-doctypes
python erp_client.py --tag qa get-permissions "Purchase Order"
python erp_client.py --tag qa query "User" --fields '["name","user_type","enabled"]'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "User" create --payload-file user.json
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com permission add "Contact" --role Auditor --permlevel 0 --confirmation-token <token> --issued-at <issued_at>
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com permission update "Contact" --role Auditor --permlevel 0 --ptype write --value 1 --confirmation-token <token> --issued-at <issued_at>
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com destructive-mutate User delete --name a@b.com --reason "left the org" --confirmation-token <token> --issued-at <issued_at>
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com create-user a@b.com "A" --roles '["Auditor"]'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com create-user a@b.com "A" --roles '["System Manager"]' --elevated-confirmation-token <token> --issued-at <issued_at>
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com config-mutate create_webhook Webhook create --identifier "https://example.com/hook" --reason "sync to billing system" --payload-file webhook.json --confirmation-token <token> --issued-at <issued_at>
```

`<token>`/`<issued_at>` pairs always come from the matching render script's
output (`render_permission_change.py`, `render_destructive_action.py`,
`render_user_draft.py`, `render_config_change.py`) — never fabricated by
the caller. A token is refused if `issued_at` is more than
`DEFAULT_TOKEN_TTL_SECONDS` (15 min) old.

`--payload-file` (not `--payload`) is the only way to pass a create/
update body — keeps JSON payloads out of shell history/`ps` output,
same convention adopted by `qkeee-erp-inventory` after its adversarial
review.

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
this retrofit, just unaudited. `destructive_mutate()`,
`gated_config_mutate()`, and `create_user()` all delegate to
`mutate_resource()` internally and inherit this logging for free.
`query_resource()`/`get_resource()` gained an opt-in `debug` kwarg
(`qkeee_erp.debug`) for `Read`-row logging, off by default.
`AUDIT_EXEMPT_DOCTYPES` prevents the logger from recursively logging
itself or double-logging the audit Comment write. This file's
`get_env_config()` also independently refuses a non-`https://` base URL
unless `QKEEE_ERP_<TAG>_ALLOW_INSECURE=1` is set — unchanged by this
retrofit, noted here only because it was nearly lost during the sync and
is worth flagging as a deliberate, skill-specific hardening not present
in every `qkeee-erp-*` copy.

**Known gap, and the one that matters most in this skill:
`call_permission_manager()` bypasses this entirely.** Permission
add/update/remove/reset POST directly to the Role Permission Manager's
own whitelisted methods — a shape `mutate_resource()` doesn't fit — so
none of the four are logged to `Qkeee Bot Audit Log`. The double-confirm
token gate and `requested_by` enforcement are unaffected; only the audit
row is missing. This is the widest-blast-radius write path in the whole
`qkeee-erp` library and currently the least audited one — flag this to
the user if they're relying on the audit trail to cover permission
changes specifically.

Full mechanism, decision log, and doctype schema:
`qkeee-erp-core/references/connector-reference.md`'s own "Audit-trail
retrofit" section and `qkeee-erp-bot-init/references/bot-doctypes-
design.md`.
