#!/usr/bin/env python3
"""
qkeee-erp-associate — accounts domain (AP/AR, Journal Entry, tax).

Ported from qkeee-erp-accounts-executive/scripts/erp_client.py during Phase
1 (connector consolidation). SURPRISE vs the refactor plan's framing: a
full function-by-function diff against qkeee-erp-frappe-core's copy found
ZERO functions in that skill's erp_client.py beyond the ~29(+8 mutate-path)
shared/core set, plus the per-skill `_cli()`/`_parse_json_arg()` variants
that every copy carries (see the consolidation report). This skill's
"double gate on submit/cancel" (noted in the plan's section 1 table) and
its JE-narration/cancel-confirmation business logic live in
render_je_draft.py / render_cancel_confirmation.py, NOT in erp_client.py —
those weren't in this Phase 1's scope (only erp_client.py copies were
diffed/ported) and still need a deliberate look in Phase 2 when this
domain's reference doc and any surviving render_*.py logic are authored/
ported.

ALLOWED_WRITE_DOCTYPES below is a first-pass allowlist inferred from this
skill's render_*.py scripts and references/ (Journal Entry via
render_je_draft.py, plus generic cancel via render_cancel_confirmation.py
which targets whatever doctype the caller names — Purchase Invoice /
Sales Invoice / Payment Entry are the ones actually referenced in the
old references/ material). Treat this as a draft to confirm against
00-conventions.md / references/domains/accounts.md once those are
authored in Phase 2, not a final capability review.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client

DOMAIN_NAME = "accounts"

# First-pass allowlist — see module docstring. Confirm/expand against
# references/domains/accounts.md once authored (Phase 2).
ALLOWED_WRITE_DOCTYPES = (
    "Journal Entry",
    "Payment Entry",
    "Purchase Invoice",
    "Sales Invoice",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """This domain's write entry point — plain mutate_resource() gated by
    ALLOWED_WRITE_DOCTYPES above (domain="accounts"). See
    core.client.mutate_resource()'s docstring for the full parameter set
    (payload, name, mode, requested_by, session_id, ...) — pass them as
    keyword arguments through **kwargs."""
    return core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)
