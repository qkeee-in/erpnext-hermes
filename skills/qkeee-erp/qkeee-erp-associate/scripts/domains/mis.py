#!/usr/bin/env python3
"""
qkeee-erp-associate — mis domain (GL / MIS reporting, read-only).

Ported from qkeee-erp-mis-analyst/scripts/erp_client.py during Phase 1
(connector consolidation). This skill's erp_client.py copy carried ZERO
functions beyond the shared core set MINUS the mutate/audit-write group
(mutate_resource, _do_mutate, _diff_fields, _audit_update, _audit_submit,
record_comment, record_audit_log_start/finish) — it was the one skill
whose copy physically omitted those, which was its read-only guarantee
pre-consolidation.

Now that core.client.mutate_resource() is one shared function used by
every domain, that physical omission is gone by construction. This module
is what replaces it, per the confirmed decision in the refactor plan
(section 2, "Read-only guarantee: runtime allowlist") and the explicit
Phase 1 instruction to implement it now, not defer it:

    ALLOWED_WRITE_DOCTYPES = ()   # deliberately empty — see mutate() below

An EMPTY allowlist means core.client.mutate_resource(..., domain="mis")
raises DoctypeNotAllowedError for every doctype, unconditionally — there
is no doctype this domain may write. mutate() below is provided only for
interface symmetry with the other domain modules; calling it always fails
closed. This is a runtime guarantee, not the old structural one — see the
plan's "Costs to manage" section (MIS's structural proof "weakens to a
runtime check") and keep it covered by the allowlist-gate tests planned
for Phase 7.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client

DOMAIN_NAME = "mis"

# Deliberately empty — see module docstring. This IS the read-only
# guarantee for this domain; do not add doctypes here without a
# deliberate decision to make MIS a writer, which contradicts its whole
# purpose.
ALLOWED_WRITE_DOCTYPES = ()

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """Always raises core.client.DoctypeNotAllowedError — ALLOWED_WRITE_DOCTYPES
    is empty by design. Provided only so this module has the same shape as
    every other domain module; MIS has no legitimate write path."""
    return core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)
