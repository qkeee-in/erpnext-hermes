# Changelog

## Generic submit/cancel confirmation-token gate + PROD env-class override + audit-failure escalation — 2026-09-01

Adversarial-review follow-up. Three code changes to `scripts/core/client.py`
plus `scripts/core/confirm_token.py`, all covered by new tests in
`scripts/core/test_client.py`:

1. **Generic advisory-first token gate for submit/cancel.** Previously only
   `fixed_assets.py`/`system_admin.py` carried bespoke double-confirm token
   schemes for their own highest-blast-radius actions — every other
   domain's plain `mutate()` wrapper (accounts, hr_payroll, sales,
   procurement, inventory) relied on prompt discipline alone to keep
   create/update and submit/cancel in separate turns. `mutate_resource()`
   gained a `DOMAIN_TOKEN_GATED_ACTIONS` registry
   (`register_domain_token_gate()`) and a shared `_require_advisory_token()`
   check; those five domains now register `{"submit", "cancel"}` and get
   the same fresh, exact-match `confirmation_token` requirement
   `gated_mutate_resource()` already enforced for its own path, computed
   via `confirm_token.py`'s new `advisory-token` CLI. fixed_assets/
   system_admin are unaffected — their own stricter per-action token
   constructors keep running exactly as before.
2. **`QKEEE_ERP_<TAG>_ENV_CLASS` override for PROD detection.** `_is_prod_tag()`
   was purely `/prod/i` name-matching — a production tag not named with
   "prod" in it silently lost every PROD-only protection. This optional
   var (`prod`/`production` vs. `nonprod`/`dev`/`test`/`qa`/`staging`/`uat`)
   lets an operator declare it explicitly; unset falls back to the
   original name-based rule unchanged.
3. **Audit-insert failure streak escalation.** A persistently failing audit
   path (bot-init never run, permission revoked, instance unreachable)
   previously produced one easy-to-miss `WARN:` line per failure,
   indistinguishable from a one-off blip. `_audit_insert()` now tracks a
   per-tag consecutive-failure streak and prints a louder, distinct
   warning once it crosses `AUDIT_FAILURE_STREAK_WARN_THRESHOLD` (3).
   Still best-effort, never blocks the real write.

Docs updated to match: `00-conventions.md` (Non-negotiable 5, GRC baseline's
new "source requester identity from the channel's own authenticated sender
field" bullet), `01-connectivity.md` (ENV_CLASS row + PROD-tag section),
`03-spec-driven-execution.md` (new section recommending Hermes' Kanban
feature over a flat spec file for work that crosses agent/session
boundaries), `SKILL.md`'s Status note (separates the now-code-enforced
gate from the still-prompt-only draft-composition renderers), and the
`accounts`/`hr_payroll`/`sales` domain module docstrings + `accounts.md`'s
procedure section.

## Spec-driven execution + ERP doc-lookup procedure — 2026-09-01

Docs-only addition: `references/03-spec-driven-execution.md` (clarify →
draft a crisp objective/plan/functional/technical spec → persist to
`./qkeee-erp-specs/` under the session's actual working directory
(`terminal.cwd` on gateway/cron, the real launch directory on local
CLI — `01-connectivity.md`'s note that local CLI ignores the
`terminal.cwd` *config key* doesn't mean it ignores the working
directory itself; `<profile>/workspace/...` is only a last-resort
fallback when neither resolves) → seek approval → fold in edits →
execute → append an Outcome section at close-out) and
`references/04-erp-doc-lookup.md` (identify the installed Frappe/ERPNext
package + version via `discover.py`, map it to its `docs.frappe.io`
subpath or GitHub README, fall back to a `discuss.frappe.io` search for
an unfamiliar functional area — never overriding live metadata or an
explicit user statement per Non-negotiable 4).

Autonomous-mode requests ("just do it," no check-ins) skip only the
interactive-approval conversation, never spec creation itself — the spec
is still drafted and persisted, marked `Approval: autonomous (<why>)` in
its header, so every autonomous run stays auditable against a written
plan the same way an interactively-approved one is.

Wired into `SKILL.md`'s activation sequence as new step 6 (after scope/
mode statement) and both files added to the `Files` list;
`00-conventions.md`'s naming table gained a `Task spec` row. No code
changed — `scripts/` and its test suite are untouched by this entry.

## RBAC pre-check reliability guard — 2026-09-01

Fix for a gap flagged and live-confirmed against a real ERPNext instance
(see the consolidation plan's §12): under a privileged (Administrator or
System-Manager-holding) bot identity, ERPNext's `frappe.client.has_permission`
does not reliably discriminate by the `user=` param it's given — it can
return `true` for a deliberately nonexistent user against a
System-Manager-only doctype. That makes `_validate_prod_requester()`'s RBAC
pre-check a no-op under such an identity: it silently rubber-stamps any
`requested_by` rather than actually checking it.

**`core/client.py`:** added `verify_rbac_precheck_reliable()`, combining a
static identity check (the connector's own bot account isn't `Administrator`
and doesn't hold `System Manager`) with a live per-tag probe
(`_probe_rbac_precheck_discriminates()` — asks `has_permission` about a
guaranteed-bogus user against `Role`/`write`, expects `false`). Wired into
`_validate_prod_requester()`: an unreliable pre-check now fails closed on
every write (`PrivilegedBotAccountError`) and warns once per tag on reads,
rather than silently trusting a result that's been shown not to discriminate.
`health_check()` surfaces the same check as `rbac_precheck_reliable` +
`rbac_precheck_warning`. Both underlying checks are cached per tag per
process — one extra round trip per tag, not per call.

**`init_bot.py`:** module docstring and `run_real()`'s final step now spell
out that this script's own elevated credentials are not the steady-state
bot account, and that whoever configures that account's real credentials
must run `health` under them and confirm `rbac_precheck_reliable: true`
before treating the environment as ready for real writes.

**`00-conventions.md`:** the bot-account bullet now states explicitly that
the bot user must never be `Administrator` or hold `System Manager` — a
code-enforced blocker via the guard above, not just a recommendation like
the existing "not a personal login" check.

13 new regression tests in `test_client.py` (`RbacPrecheckReliabilityTests`)
cover the probe, the identity check, both combined, and the guard's
wiring into `_validate_prod_requester()`/`mutate_resource()`/`health_check()`.
Full suite: 90 tests + 20 subtests passing.

## Phase 8 (handoff) — 2026-08-31

Per the consolidation plan §11 item 8 ("update the repo index/README, single
changelog entry noting the 11→1 consolidation and the doctype removal").
This is that entry — a summary for anyone landing on this file without the
plan doc in hand; the phase-by-phase detail below (Phases 7 down to 3)
remains the record of *how* each step was done.

**What changed, end to end:** the `qkeee-erp` skill family — 11 separate
directories (`qkeee-erp-frappe-core`, `-bot-init`, `-accounts-executive`,
`-hr-associate`, `-inventory`, `-procurement`, `-sales`,
`-fixed-asset-manager`, `-mis-analyst`, `-system-admin`,
`-doc-extraction`), each a synced-by-script near-copy of the same
connector — is now one skill, `qkeee-erp-associate`: one `client.py`
(RBAC pre-check + write-allowlist gate + PII redaction + audit logging,
all unconditional on every tag, not just PROD/debug), one persona voice,
and per-domain procedure/logic split into `references/domains/*.md` +
`scripts/domains/*.py` (`hr_payroll`, `accounts`, `mis`, `sales`,
`procurement`, `inventory`, `fixed_assets`, `system_admin`, plus new
`manufacturing` coverage and `doc-extraction`, neither of which existed
as a working write path before). `sync_to_personas.py` and the 10
superseded directories are gone (Phase 6); CI's `sync-check` job (which
called it) is removed. CI's `tests` job was also silently broken since
Phase 6 — the `unittest discover -p "test_*.py"` command it invoked from
`scripts/` never matched anything, because the associate's suites live
one level down in `scripts/core/`/`scripts/domains/` and use bare local
imports (`import client`, not `from core import client`) that only
resolve via `conftest.py`'s sys.path bootstrap — a pytest-only mechanism,
per that file's own docstring (Phase 7). Fixed by switching the job to
`python -m pytest scripts` (run from the skill root, matching the
documented invocation), confirmed locally: 77 tests + 20 subtests pass.

**Doctype removal:** `Qkeee Bot Persona` (write-only, never read back,
never foreign-keyed to Audit Log) is gone — schema + `PERSONA_MANIFEST`
dropped, existing-row schema exported to Phase 3's entry below for manual
audit reference. The debug-only `Qkeee Bot Session`/`Qkeee Bot Message`
doctypes went with the `_DEBUG` flag they depended on (Phase 5 — read
audit logging is now unconditional, so there's no verbose/quiet mode left
to gate them). `Qkeee Bot Audit Log` is the sole surviving audit doctype;
its `persona_code` field is repointed to `domain_code`.

**Repo-level docs updated to match:** top-level `README.md` (directory
layout, skill/domain table, credentials section, audit-trail doctype
table, safety/governance section — all previously describing the 11-skill
layout), `distribution.yaml`'s description, and `.github/workflows/ci.yml`
(dropped the dead sync-check job, fixed the tests job's test-discovery
guard). No live ERPNext instance touched by this phase.

## Phase 7 (test & validate) — 2026-08-31

Per the consolidation plan §11 item 4 ("run an end-to-end smoke pass per
domain against the demo instance"). Live pass against `https://demo.qkeee.in`
using tag `demo` (`QKEEE_ERP_DEMO_*`, `requested_by=demo.admin@qkeee.in`,
`mode=read-write`). `scripts/core/test_client.py` +
`scripts/core/test_memory_promote.py` re-run after the live poking: still
76 tests + 20 subtests passing, unaffected.

### Read round-trip: PASS for all 8 domains

`get_resource()` against a doctype central to each domain, with
`requested_by` set, succeeded end-to-end (RBAC pre-check + connector) for
all 7 writer domains plus `mis`: `accounts`→Journal Entry
`ACC-JV-2026-00001`, `fixed_assets`→`Asset` (empty list, connector still
round-trips cleanly — no Asset exists on this instance), `hr_payroll`→
Employee `HR-EMP-00001`, `inventory`→Item `SKU001`, `procurement`→Purchase
Order `PUR-ORD-2026-00001`, `sales`→Sales Order `SAL-ORD-2026-00001`,
`system_admin`→Role `System Manager`, `mis`→Company `Antigra Systems Pvt
Ltd` plus a `run_query_report()` "Trial Balance" run (fiscal year
`2026-2027`, see finding below) — both succeeded.

### Write round-trip: BLOCKED for all 7 writer domains — a live connector/instance incompatibility, not a domain-specific failure

Every `domain.mutate(..., action="create", requested_by=...)` attempt (one
per writer domain: Journal Entry, Asset, Employee, Material Request,
Supplier, Customer, Webhook) failed identically, **before any HTTP write
reached ERPNext and before any Qkeee Bot Audit Log row was written** —
confirmed via `resource_exists()` on every attempted test-record name
afterward (all `False`) and via re-listing the audit log (no new rows).
Root cause, reproduced directly: `mutate_resource()`'s RBAC pre-check
(`_validate_prod_requester()` → `check_user_permission()` →
`GET /api/method/frappe.client.has_permission`) omits `docname` from the
query string whenever the caller has no record name yet — true for every
`create` action (no name exists pre-insert) and also for `query_resource()`
list-level reads (no single record in question). This instance's Frappe
build's `frappe.client.has_permission` has no default for its `docname`
parameter, so the omission 500s with `TypeError: has_permission() missing 1
required positional argument: 'docname'` — a generic, doctype-independent
failure, confirmed identical across all 7 create attempts and against a
plain `query_resource(..., requested_by=...)` list call. `get_resource()`
and `run_query_report()` are unaffected because they always pass a
concrete `docname`/`report_name`. **Net effect: as currently coded, no
domain can create a new record, and no caller can list-query anything, once
a `requested_by` is supplied against this instance** — this is new since
Phase 5 (the RBAC pre-check didn't run universally before that). Pre-Phase-5
`Qkeee Bot Audit Log` rows on this instance (tag `demo-erp`, a different,
older environment tag) show real `Create`/`Update` attempts reaching
ERPNext's own validation errors, confirming this 500 is a regression
introduced by the pre-check, not a pre-existing instance limitation.

No test records were created and none needed cleanup — the pre-check
failure means every write attempt aborted before any ERPNext side effect.

### Follow-up: root cause fixed, write round-trip re-confirmed PASS

`check_user_permission_raw()` now always sends `docname` (empty string
when no record exists yet) instead of omitting the param when falsy.
Reproduced the 500 directly via `curl` against `frappe.client.has_permission`
first to confirm the exact fix shape before changing code: omitting
`docname` 500s (`TypeError: has_permission() missing 1 required positional
argument: 'docname'`); `docname=""` returns a correct doctype-level
`{"message": {"has_permission": true}}`; `docname=None`-the-literal-string
incorrectly 404s (`"Sales Order None not found"`) — confirming empty string,
not `None`-as-text, is the right shape for this Frappe build. Fixed in
`scripts/core/client.py`; `scripts/core/test_client.py`'s stale test
(`test_docname_omitted_when_not_given`, which asserted the now-wrong
omit-when-falsy behavior) updated to assert `docname=""` instead.

Re-ran the write round-trip live against `demo` after the fix:
`procurement.mutate("demo", "Supplier", "create", payload={"supplier_name":
"QKEEE-SMOKE-TEST-Supplier", ...}, mode="read-write",
requested_by="demo.admin@qkeee.in")` succeeded end-to-end — record created
(`QKEEE-SMOKE-TEST-Supplier`), then `procurement.mutate(..., "delete", ...)`
cleaned it up immediately after. **Nothing left behind.** Confirms the fix
resolves the create-path blocker for all 7 writer domains (same code path,
`Supplier` chosen because a never-referenced Supplier deletes cleanly per
prior live findings — see `qkeee-erp-demo-instance.md`). The
Qkeee-Bot-Audit-Log 403 (below) is unaffected by this fix — a separate,
already-understood limitation of this session's non-bot credentials.

Also live-observed and fixed in the same pass: `_audit_submit(log_name)`
was being called even when the preceding insert already failed and
returned `None` (the 403 case below) — `urllib.parse.quote(None)` then
raised a second, confusing `"quote_from_bytes() expected bytes"` warning
that masked the real, already-reported cause. Added an early `if not
log_name: return False` guard, plus a regression test
(`AuditSubmitSkipsWhenInsertFailedTests`).

Full suite re-run after both fixes: 77 tests + 20 subtests passing.

### MIS refusal: PASS

`mis.mutate(tag, "Customer", "create", ...)` raised
`DoctypeNotAllowedError` immediately (`ALLOWED_WRITE_DOCTYPES` gate runs
before the RBAC pre-check in `mutate_resource()`, so it's unaffected by the
bug above) — confirms the empty-allowlist refusal is real and unconditional
against a live instance, no write attempted.

### Qkeee Bot Audit Log: provisioned, but this session's bot account can't write to it

`Qkeee Bot Audit Log` doctype exists on this instance (confirmed via
`DocType` resource GET) and has real historical rows (tag `demo-erp`,
pre-Phase-5). Under tag `demo`'s credentials, every audit-log insert this
session attempted 403'd: `"User demo.admin@qkeee.in does not have doctype
access via role permission for document Qkeee Bot Audit Log"`. The
historical `demo-erp` rows recorded `requested_by: demo.admin@qkeee.in`
too, but on those the *authenticating* API identity was presumably a
proper dedicated bot account (`hermes-bot@qkeee.in` exists as a separate
User on this instance) — this session's `QKEEE_ERP_DEMO_API_KEY` instead
authenticates directly as `demo.admin@qkeee.in`, a human account without
the `Qkeee Bot` role, so it can request writes but can't self-audit them.
Best-effort logging degraded silently as designed (no write was blocked by
this), but it means this session produced zero new Audit Log rows,
successful or failed.

### Other live findings

- **This instance was reset/reseeded since the 2026-08-16 memory note.**
  Companies are no longer "Enfasco Inc." / "Qkeee LLP" — now **Antigra
  Systems Pvt Ltd** (ASPL), **Mapro Industries Pvt Ltd** (MIPL), **Mapro
  Industries Pvt Ltd (Demo)** (MIPLD), and **Sharad HUF**, all INR. Real
  transactional data (Journal Entries, Purchase/Sales Orders, Employees,
  Items `SKU001`-`SKUxxx`) exists under these new companies.
  `frappe.auth.get_logged_user` health-check still returns
  `demo.admin@qkeee.in` as before.
- Fiscal Year on this instance is named `2026-2027` (Apr–Mar), not a plain
  `2026` — a query-report filter using the calendar year as fiscal_year
  fails with `ValidationError: Fiscal Year 2026 does not exist`.
- `hermes-bot@qkeee.in` exists as a distinct `System User` from
  `demo.admin@qkeee.in` — likely the intended dedicated bot account per
  this connector's own bot-account design note in `client.py`'s module
  docstring; this session's demo credentials are not that account.

## Phase 6 (delete old skills) — 2026-08-31

Per the consolidation plan §11 item 6 ("Delete the old skills"). `git rm -r`
against the 10 superseded skill directories: `qkeee-erp-accounts-executive`,
`qkeee-erp-bot-init`, `qkeee-erp-doc-extraction`,
`qkeee-erp-fixed-asset-manager`, `qkeee-erp-frappe-core`,
`qkeee-erp-hr-associate`, `qkeee-erp-inventory`, `qkeee-erp-mis-analyst`,
`qkeee-erp-procurement`, `qkeee-erp-sales`, `qkeee-erp-system-admin`.
`sync_to_personas.py` (lived in `qkeee-erp-frappe-core/scripts/`) and the
stray HR-test JSON artifacts (lived in `qkeee-erp-procurement/scripts/`)
went with their parent directories — nothing separate to target. Untracked
`__pycache__`/`.pytest_cache` leftovers under those 10 dirs were removed
directly (`rm -rf`), not through git, since they were never tracked.

Also removed the top-level `skills/qkeee-erp/qkeee-erp.env.example` — not
named in the plan's Phase 6 bullet, but orphaned by the same consolidation:
§4 already renamed it into
`qkeee-erp-associate/qkeee-erp-associate.env.example`, and nothing
referenced the old top-level copy after the 10 consumer skills were gone.

Nothing here touched a live ERPNext instance. `qkeee-erp-associate`'s own
test suite (`scripts/core/test_client.py` + `test_memory_promote.py`) was
re-run after the deletion and still passes (71 tests + 2 subtests) —
confirms the associate skill was never importing anything from a sibling
directory.

## Phase 5 (GRC hardening) — 2026-08-31

Per the consolidation plan §9 ("GRC & compliance hardening") and §11 item 5
("Harden GRC"). **Code-only** — no live ERPNext instance was touched;
`scripts/core/client.py`'s unit tests (`scripts/core/test_client.py`) were
run locally and pass (58 tests + 2 subtests → 71 across the module with
`test_memory_promote.py`), nothing here was exercised against
`demo.qkeee.in` or any other target.

### `_validate_prod_requester()` — RBAC pre-check now runs on every tag

Previously the requester-permission check (resolve `requested_by` as a
real ERPNext `User`, then confirm `frappe.client.has_permission` via
`check_user_permission()`) only ran when `_is_prod_tag(tag)` was true.
Now: whenever a `requested_by` is present — on ANY tag — it gets that same
validation. Presence of `requested_by` stays mandatory on PROD only
(unchanged from Phase 1: the `QKEEE_ERP_<TAG>_REQUESTED_BY` env-var
default is still refused on PROD, a PROD call must still pass an explicit
requester). Function/exception/constant names
(`_validate_prod_requester()`, `UnvalidatedProdRequesterError`,
`PROD_GATE_EXEMPT_DOCTYPES`) are kept as-is from their Phase 1 PROD-only
origin, per `references/00-conventions.md`'s GRC baseline, which already
specced this landing under these same names — don't read the name as
scope. This closes the prior gap where a bogus or unauthorized
`requested_by` was silently accepted on any non-PROD tag.

### Read audit logging — unconditional, `debug`/`_DEBUG` removed

`query_resource()`, `get_resource()`, and `run_query_report()` now call
`_log_read()` unconditionally. The `debug` keyword-only parameter on all
three, `get_env_config()`'s `debug_default` field, the
`QKEEE_ERP_<TAG>_DEBUG` env var, the CLI's `--debug` flag, and
`_parse_bool_env()` (now dead once nothing else used it) are all removed
— left as a no-op flag rather than a real toggle would have been more
confusing than deleting it outright. No call site outside
`scripts/core/client.py`/its own CLI passed `debug=`, so this needed no
changes in `scripts/domains/*.py`.

### Not touched this phase

- `redact_pii()` — plan §9 confirms this was already single-source as of
  Phase 1; no code change needed.
- `non-erpnext-adapter.md` — already shipped (Phase 2).
- Marking `qkeee-erp-associate` externally-owned/pinned against Hermes'
  autonomous background-review pass — this is a target Hermes profile's
  `config.yaml` decision (`skills.external_dirs` / trusted project-local
  skill dirs, see `agent/skill_utils.py`'s `is_external_skill_path()`),
  not something this skill's own code or frontmatter can set on itself.
  Documented as an operator action item in `references/00-conventions.md`;
  whoever owns the deployment's Hermes profile config needs to confirm
  this skill's install path resolves under one of those two.

## Phase 3 (doctype migration) — 2026-08-31

Per the consolidation plan §7 ("Doctype cleanup") and §11 item 3
("Migrate doctypes"). **Code-only** — no live ERPNext instance was
touched by this change; nothing below was run against `demo.qkeee.in` or
any other target. This is a record of what the code now does differently
if/when `scripts/init_bot.py` is eventually run for real.

### Removed: `Qkeee ERP Bot Persona` doctype + `PERSONA_MANIFEST`

Confirmed dead weight (plan §7): every skill only ever wrote this
doctype (a fire-and-forget `register-persona` upsert), nothing read it
back for a functional decision, and it wasn't even foreign-keyed to Audit
Log — correlation was a plain string match. Dropped entirely from
`scripts/doctype_defs.py` (no more `PERSONA` dict, no more
`PERSONA_MANIFEST` list, no more `ensure_personas()`/`register-persona`
step in `scripts/init_bot.py`).

**Exported for manual-audit reference** (plan §7: "export existing rows
to a one-line changelog note first, in case of manual audit reference")
— this is the schema and manifest as they existed in
`qkeee-erp-bot-init/scripts/doctype_defs.py` immediately before removal:

```python
# Doctype schema (Qkeee ERP Bot Persona), as it existed pre-removal:
PERSONA = {
    "doctype": "DocType",
    "name": "Qkeee Bot Persona",
    "module": "Custom",
    "custom": 1,
    "naming_rule": "By fieldname",
    "autoname": "field:persona_code",
    "track_changes": 0,
    "fields": [
        {"fieldname": "persona_code", "fieldtype": "Data", "reqd": 1, "unique": 1},
        {"fieldname": "persona_label", "fieldtype": "Data", "reqd": 1},
        {"fieldname": "default_mode", "fieldtype": "Select",
         "options": "Read Only\nRead Write", "default": "Read Only"},
        {"fieldname": "non_negotiables", "fieldtype": "Text"},
        {"fieldname": "active", "fieldtype": "Check", "default": "1"},
    ],
    # permissions: Qkeee Bot role read-only; System Manager read/write/create,
    # no delete (a decommissioned persona was disabled via `active`, never removed).
}

# PERSONA_MANIFEST, as it existed pre-removal (persona_code / persona_label):
PERSONA_MANIFEST = [
    {"persona_code": "qkeee-erp-accounts-executive", "persona_label": "Accounts Executive"},
    {"persona_code": "qkeee-erp-fixed-asset-manager", "persona_label": "Fixed Asset Manager"},
    {"persona_code": "qkeee-erp-hr-associate", "persona_label": "HR Associate"},
    {"persona_code": "qkeee-erp-inventory", "persona_label": "Inventory"},
    {"persona_code": "qkeee-erp-mis-analyst", "persona_label": "MIS Analyst"},
    {"persona_code": "qkeee-erp-procurement", "persona_label": "Procurement"},
    {"persona_code": "qkeee-erp-sales", "persona_label": "Sales"},
    {"persona_code": "qkeee-erp-system-admin", "persona_label": "System Admin"},
]
```

If a target instance was previously initialized by
`qkeee-erp-bot-init` (i.e. actually has live `Qkeee Bot Persona` rows from
before this consolidation), those rows are **not** touched by this
change — this is a code-only edit to what a *future* provisioning run
would create, not a migration script against existing data. Manually
reviewing/decommissioning any live `Qkeee Bot Persona` rows on a
previously-initialized instance is a separate, deliberate action for
whoever operates that instance; nothing in `qkeee-erp-associate` does it
for them.

### Repointed: `Qkeee ERP Bot Audit Log`'s `persona_code` → `domain_code`

Same denormalized-string convention (no doctype join) — the field now
names the active `qkeee-erp-associate` domain reference that made a given
call (e.g. `qkeee-erp-associate/hr-payroll`) instead of a separate
installed persona skill's name (e.g. `qkeee-erp-hr-associate`). Changed
in `scripts/doctype_defs.py`'s `AUDIT_LOG` field list only — this is a
schema-definition change for a *fresh* provisioning run, not a live
rename of an already-provisioned field on an existing instance. A target
instance that already has `Qkeee Bot Audit Log` provisioned with the old
`persona_code` field name keeps that field as-is until someone
deliberately renames it server-side (Frappe's own Customize Form / a
direct DocType field rename) — `scripts/init_bot.py`'s existence-check
(`resource_exists(tag, "DocType", "Qkeee Bot Audit Log")`) will report the
doctype as already present and skip re-creating it, so this code change
alone does not retroactively rename the field on a live instance.

### `scripts/init_bot.py` — dropped persona provisioning

`ensure_personas()`, the `register-persona` upsert step, and every
`--bot-email`-adjacent bot-user provisioning code path from the old
`qkeee-erp-bot-init/scripts/init_bot.py` are **not** ported into this
skill's `scripts/init_bot.py`. Per the Phase 3 task's explicit scope, this
skill's `init_bot.py` now provisions only the `Qkeee Bot` Role and the
`Qkeee Bot Audit Log` doctype (with the renamed field) — dry-run/confirm-
token flow preserved, same code-enforced discipline as before, just a
narrower plan. Bot-user provisioning (`ensure_bot_user.py`'s
create-or-update-user-and-generate-keys flow) is not yet ported into this
skill at all — it remains only in `qkeee-erp-bot-init` for now, a
follow-up scoping decision for whoever picks up the remaining bot-init
surface area, not attempted here to avoid scope creep beyond "doctype code
changes" in this phase.
