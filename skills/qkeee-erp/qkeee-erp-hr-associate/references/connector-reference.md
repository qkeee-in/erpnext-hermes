# qkeee-erp-hr-associate connector reference

This skill's copy of the `qkeee-erp` connector layer, synced from the
canonical version in `qkeee-erp-core/references/connector-reference.md`.
Carries the full read+write path (`mutate_resource`) for this persona's
read-write-capable capabilities (Employee update, Leave Application,
Attendance, Job Opening, Job Applicant, Interview, Employee Separation).
**Job Offer and Employee Onboarding are never called through
`mutate_resource()`'s create/submit path by this skill's own logic** —
see `SKILL.md`'s non-negotiable and `scripts/render_advisory_draft.py`.

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

**Submit is two calls, not one** — see `mutate_resource()`'s docstring
and `qkeee-erp-core`'s canonical reference for why. **Response shape
differs by action** (`create`/`update`/GET → `data`; `submit`/`cancel`
→ `message`) — same finding as every other `qkeee-erp-*` skill's build;
reconfirmed live here during the Leave Application round trip.

## Live validation record

**This skill's connector was validated live against `<erp-instance>`**
via a temporary API key/secret (session login +
`frappe.core.doctype.user.user.generate_keys`; revoked immediately
after — `PUT /api/resource/User/Administrator` with `{"api_key":
null}`, reconfirmed the old token 401s):

1. **Employee** — `mutate create` with only ERPNext's schema-mandatory
   fields succeeded immediately (`HR-EMP-00002`, `docstatus: 0`,
   confirmed **not submittable**). Not independently deletable
   afterward (see "Known link chains" below).
2. **Leave Application** — `mutate create` succeeded with `status:
   "Open"` (the default); `mutate submit` failed twice before
   succeeding, surfacing two live-only gates not visible in the
   schema's `reqd` flags — see `references/erpnext-hr-docs.md` for the
   exact error text and fix for each. After both were resolved
   (`status` updated to `Approved`, `holiday_list` set on the
   Employee), submit succeeded (`docstatus: 1`), then cancel succeeded
   (`docstatus: 2`).
3. **Job Applicant** — `mutate create` with only schema-mandatory
   fields (`applicant_name`, `email_id`, `status`) succeeded
   immediately, confirmed **not submittable**, confirmed **autonamed by
   `email_id`** (not a generated series). Deleted cleanly afterward
   (never referenced downstream) — `DELETE` → `{"data": "ok"}`, HTTP
   202, same clean-delete shape `qkeee-erp-procurement`'s build found
   for a never-referenced Supplier.

**Known link chains from this validation — informs the "describe
cancel, not delete" guidance for anything ledger/process-linked:**
`DELETE` on the test Employee (`HR-EMP-00002`) failed with
`LinkExistsError` because of the (now-cancelled) Leave Application
referencing it; a further attempt to `DELETE` the cancelled Leave
Application itself failed with a **second** `LinkExistsError`, this
time because of the auto-created Attendance record
(`HR-ATT-2026-00001`) referencing it — even though that Attendance
record was itself already auto-cancelled (`docstatus: 2`) as a side
effect of the Leave Application's cancel. Cancellation does not remove
a link-existence check on delete; a chain of auto-generated records
(Leave Application → Attendance) each block deletion of the one before
them even once every record in the chain is cancelled. Test data left
in place: `HR-EMP-00002` (labeled via its `bio` field — Employee has no
`user_remark`-equivalent field, confirmed live; `bio` was used
instead), cancelled `HR-LAP-2026-00001`, and the auto-cancelled
`HR-ATT-2026-00001`.

## The read-only/read-write gate

`mutate_resource()` takes `mode` as an explicit parameter and refuses
any create/update/submit/cancel/delete unless `mode == "read-write"` —
the library-wide gate, identical to every other `qkeee-erp-*` skill's
copy. It is **not** the same as this skill's advisory-first override
for Job Offer / Employee Onboarding — `mutate_resource()` has no
awareness of doctype-specific policy at all; the calling skill's own
logic (via `scripts/render_advisory_draft.py`) is what must never route
those two doctypes' create/submit calls through this shared function in
the first place, in any mode.

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
`has_more` — a headcount or attendance report that silently drops rows
past the default limit misreports the actual numbers, not something the
connector prevents by itself.

## Harness capability discovery

Before assuming this bundled `urllib`-based script is the only option,
check whether the host harness already exposes an HTTP-capable tool and
prefer that. Degrade gracefully to this script if discovery isn't
supported.

## CLI usage

```
python erp_client.py list-envs
python erp_client.py --tag qa health
python erp_client.py --tag qa query "Employee" --filters '[["status","=","Active"]]' --fields '["name","employee_name","department"]'
python erp_client.py --tag qa query "Leave Application" --filters '[["employee","=","HR-EMP-00042"],["status","=","Approved"]]'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Employee" update --name "HR-EMP-00042" --payload '{"department": "Engineering"}'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Leave Application" create --payload '{"...": "..."}'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Leave Application" update --name "HR-LAP-2026-00001" --payload '{"status": "Approved"}'
python erp_client.py --tag qa --mode read-write --requested-by priya@org.com mutate "Leave Application" submit --name "HR-LAP-2026-00001"
```

Note: no `mutate ... create` call is ever issued against `Job Offer` or
`Employee Onboarding` by this skill's own logic — see `SKILL.md`.

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
