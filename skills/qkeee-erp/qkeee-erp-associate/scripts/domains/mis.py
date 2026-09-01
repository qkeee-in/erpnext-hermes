#!/usr/bin/env python3
"""
qkeee-erp-associate — mis domain (GL / MIS reporting, read-only).

core.client.mutate_resource() is one shared function used by every domain,
so MIS's read-only posture is enforced here, at the domain layer, rather
than by the connector lacking a mutate path:

    ALLOWED_WRITE_DOCTYPES = ()   # deliberately empty — see mutate() below

An EMPTY allowlist means core.client.mutate_resource(..., domain="mis")
raises DoctypeNotAllowedError for every doctype, unconditionally — there
is no doctype this domain may write. mutate() below is provided only for
interface symmetry with the other domain modules; calling it always fails
closed. This is a runtime guarantee — keep it covered by
scripts/domains/test_allowlist_gates.py's allowlist-gate tests.
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
