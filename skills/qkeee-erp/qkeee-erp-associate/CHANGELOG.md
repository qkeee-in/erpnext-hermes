# Changelog

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
