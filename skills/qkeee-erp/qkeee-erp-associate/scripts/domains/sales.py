#!/usr/bin/env python3
"""
qkeee-erp-associate — sales domain (Customer, Quotation, Sales Order,
Delivery Note).

Ported from qkeee-erp-sales/scripts/erp_client.py during Phase 1
(connector consolidation). Same finding as accounts.py/hr_payroll.py: a
full function-by-function diff against qkeee-erp-frappe-core's copy found
ZERO functions in this skill's erp_client.py beyond the shared core set
(plus the per-skill `_cli()`/`_parse_json_arg()` variants every copy
carries). The "Quotation submit hardcoded advisory" behavior noted in the
plan's section 1 table lives in render_quotation_draft.py, not in
erp_client.py — out of this Phase 1's scope.

ALLOWED_WRITE_DOCTYPES is a first-pass allowlist inferred from
render_customer_draft.py/render_quotation_draft.py's target doctypes plus
Sales Order/Delivery Note referenced across references/. Confirm/expand
against references/domains/sales.md once authored (Phase 2).
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client

DOMAIN_NAME = "sales"

ALLOWED_WRITE_DOCTYPES = (
    "Customer",
    "Quotation",
    "Sales Order",
    "Delivery Note",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """This domain's write entry point — plain mutate_resource() gated by
    ALLOWED_WRITE_DOCTYPES above (domain="sales")."""
    return core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)
