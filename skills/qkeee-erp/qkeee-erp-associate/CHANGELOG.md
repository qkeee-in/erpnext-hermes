# Changelog

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
