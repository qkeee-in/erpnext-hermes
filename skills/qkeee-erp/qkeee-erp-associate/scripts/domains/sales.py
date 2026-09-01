#!/usr/bin/env python3
"""
qkeee-erp-associate — sales domain (Customer, Quotation, Sales Order,
Delivery Note).

"Quotation submit hardcoded advisory" belongs in render_quotation_draft.py
— that advisory-draft script doesn't exist in this skill's scripts/ yet,
so that behavior isn't enforced in code today.

ALLOWED_WRITE_DOCTYPES covers render_customer_draft.py/
render_quotation_draft.py's target doctypes plus Sales Order/Delivery
Note. Cross-check against references/domains/sales.md before expanding.
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
