#!/usr/bin/env python3
"""
qkeee-erp-associate — procurement domain (Supplier, PO, RFQ).

ALLOWED_WRITE_DOCTYPES covers render_po_draft.py/render_supplier_draft.py's
target doctypes plus Request for Quotation/Supplier Quotation. Cross-check
against references/domains/procurement.md before expanding.
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
