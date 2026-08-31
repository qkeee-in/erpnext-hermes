"""qkeee-erp-associate domain modules (Phase 1 consolidation).

Each module here corresponds to one of the domain slugs in
00-conventions.md's fixed enum (hr-payroll, accounts, mis, sales,
procurement, inventory, manufacturing, fixed-assets, system-admin,
doc-extraction, grc-audit) and declares that domain's ALLOWED_WRITE_DOCTYPES
— see core.client.register_domain_allowlist()/mutate_resource()'s `domain`
parameter for the write-allowlist gate this feeds.

`manufacturing.py` is intentionally absent — no current skill covers it
(a documented gap, see the consolidation plan's Risks section); it's a
Phase 2+ concern. `doc_extraction.py` is also absent — that skill has no
connector at all (no erp_client.py to extract from).
"""
