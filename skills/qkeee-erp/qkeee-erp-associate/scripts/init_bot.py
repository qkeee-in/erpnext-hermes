#!/usr/bin/env python3
"""
qkeee-erp-associate — init_bot (admin-invoked, one-time provisioning helper).

Per the consolidation plan section 7: "Bot-init's provisioning script
survives as scripts/init_bot.py inside the unified skill — an admin-
invoked, one-time action, not part of the associate's normal conversational
flow." This module carries qkeee-erp-bot-init/scripts/erp_client.py's ONE
function that fell outside the shared core set (see the consolidation
report's diff): ensure_qkeee_env_file_skeleton().

SCOPE NOTE: this is deliberately narrow — it is NOT a port of bot-init's
full provisioning workflow (doctype/role creation, ensure_bot_user.py,
confirm_token.py's dry-run double-confirm, doctype_defs.py, etc; see
qkeee-erp-bot-init/scripts/init_bot.py for that). Phase 1's task was
scoped to erp_client.py copies only. The full provisioning orchestration
script is Phase 2+ work (when doctype_defs.py/init_bot.py proper are
ported per section 7 of the plan, dropping the persona manifest along the
way) — porting it here now would conflate connector consolidation with
doctype-migration scope this phase explicitly defers.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.client import _qkeee_env_file_path


def ensure_qkeee_env_file_skeleton() -> bool:
    """Create qkeee-erp.env with a header comment ONLY (no tag lines, no
    secrets) if it doesn't already exist. Returns True if it was created,
    False if it was already there.

    Deliberately does NOT write BASE_URL/API_KEY/API_SECRET into this
    file, even when this process has them (e.g. from os.environ) or just
    generated new ones via ensure_bot_user.py — credentials only ever land
    in this file via the operator's own manual copy-paste from a one-time
    stdout print, never by this tooling reading them back and re-writing
    them. This function exists only so a user running init_bot on a truly
    fresh profile isn't left hunting for where to put the lines it tells
    them to add — it's the empty-file convenience half of that
    instruction, not a partial reversal of it."""
    path = _qkeee_env_file_path()
    if os.path.exists(path):
        return False
    header = (
        "# qkeee-erp.env — ERPNext credentials for the qkeee-erp-associate skill.\n"
        "# Created by init_bot.py. Add three lines per environment tag:\n"
        "#   QKEEE_ERP_<TAG>_BASE_URL=https://org.erpnext.com\n"
        "#   QKEEE_ERP_<TAG>_API_KEY=...\n"
        "#   QKEEE_ERP_<TAG>_API_SECRET=...\n"
        "# Optional per-tag: QKEEE_ERP_<TAG>_DEBUG, QKEEE_ERP_<TAG>_REQUESTED_BY.\n"
        "# Never committed, never read back by this tooling — you paste values in\n"
        "# yourself, once, after ensure_bot_user.py prints a freshly generated key.\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
    return True


if __name__ == "__main__":
    created = ensure_qkeee_env_file_skeleton()
    print(f"{'created' if created else 'already present'}: {_qkeee_env_file_path()}")
