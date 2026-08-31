#!/usr/bin/env python3
"""
qkeee-erp-associate — procurement domain (Supplier, PO, RFQ).

Ported from qkeee-erp-procurement/scripts/erp_client.py during Phase 1
(connector consolidation). Same finding as accounts.py/hr_payroll.py/
sales.py: a full function-by-function diff against qkeee-erp-frappe-core's
copy found ZERO functions in this skill's erp_client.py beyond the shared
core set (plus the per-skill `_cli()`/`_parse_json_arg()` variants every
copy carries). Note: this skill's scripts/ directory also had 15 stray
leftover HR test-fixture JSON files (emp_create.json, ja_create.json,
lap_*.json, leave_create.json, ...) per the plan's section 1 table —
those are a cleanup item for Phase 6 (deleting the old skill directories
outright makes this moot), not touched here.

ALLOWED_WRITE_DOCTYPES is a first-pass allowlist inferred from
render_po_draft.py/render_supplier_draft.py's target doctypes plus
Request for Quotation/Supplier Quotation referenced across references/.
Confirm/expand against references/domains/procurement.md once authored
(Phase 2).
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client

DOMAIN_NAME = "procurement"

ALLOWED_WRITE_DOCTYPES = (
    "Supplier",
    "Purchase Order",
    "Request for Quotation",
    "Supplier Quotation",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """This domain's write entry point — plain mutate_resource() gated by
    ALLOWED_WRITE_DOCTYPES above (domain="procurement")."""
    return core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)
