"""Lets the whole suite run from one place: `cd qkeee-erp-associate &&
python -m pytest scripts` (or just `pytest` from the skill root, since
pytest walks down into scripts/).

Every test module under scripts/core and scripts/domains uses bare local
imports (`import client`, `import accounts`, ...) rather than package-
qualified ones (`from core import client`), matching the sys.path
bootstrap every scripts/domains/*.py module already does at import time
for its own `from core import client` line. Without this conftest adding
both directories to sys.path up front, those bare imports only resolve
when pytest's cwd is already the test's own directory (python -m pytest
adds cwd to sys.path[0] itself, nothing else does) — running from the
skill root would fail with ModuleNotFoundError otherwise.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _sub in ("core", "domains"):
    _path = os.path.join(_SCRIPTS_DIR, _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)
