"""qkeee-erp-associate — core connector package (Phase 1 consolidation).

Holds the single shared ERPNext (Frappe REST API) client (`client.py`) and
its confirmation-token dependency (`confirm_token.py`). Domain modules under
`scripts/domains/` import from `core.client` — see that module's docstring
and the refactor plan (qkeee-erp-associate-consolidation-plan.md, section 6)
for the write-allowlist gate design.
"""
