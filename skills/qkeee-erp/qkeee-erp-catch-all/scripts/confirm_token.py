#!/usr/bin/env python3
"""
qkeee-erp-catch-all — confirmation-token helper.

This skill's non-negotiable-on-top-of-the-non-negotiable is "advisory-
first, always": every write-capable capability stages a draft and shows
the exact payload before doing anything else, regardless of
`qkeee_erp.mode` — because unlike the eight named persona skills, no
human reviewed catch-all's capability table at design time (the doctype
wasn't known in advance). Before this file existed, that rule lived only
in prose (SKILL.md step 8) with no code artifact tying the render step to
the execute step — nothing stopped a caller from constructing a payload
and calling `mutate_resource()` directly in the same turn, skipping the
draft-and-show step entirely.

This module closes that gap the same way system-admin/fixed-asset-manager
already close theirs: `render_draft.py` computes a token from the exact
(doctype, action, name, payload, requested_by) facts just shown to the
user. `erp_client.gated_mutate_resource()` recomputes the same token from
the actual call's own arguments and refuses to proceed without a match —
a caller cannot skip straight from "I built a payload" to "I wrote it"
without round-tripping the rendered facts back in. Unlike the other two
skills (which gate only their highest-risk actions), catch-all gates
every create/update/submit/cancel/delete through this same mechanism,
because — per this skill's own domain-knowledge.md — nothing here has had
the design-time review that lets the named personas be more assertive.

Same limitation as system-admin/fixed-asset-manager's copies, stated
plainly: a matching token proves the call's facts are IDENTICAL to what
was rendered and that the render happened recently (see is_fresh()). It
does NOT prove a human actually read and approved the draft — nothing in
this file (or in erp_client.py) can observe that. The calling skill's own
discipline (never compute a token and immediately consume it in the same
turn; only use a token after the user's own reply in the transcript
affirmatively confirms the specific rendered draft) is what makes
"advisory-first" real. Treat the token as a tamper/staleness check, not a
human-presence proof.
"""

import hashlib
import json
import time

# Same rationale as the other qkeee-erp-* skills' confirm_token.py: long
# enough for a human to read a rendered draft and reply, short enough
# that a token from a stale/abandoned conversation can't be replayed
# days later against what may now be different live data.
DEFAULT_TOKEN_TTL_SECONDS = 900  # 15 minutes

# Clock-skew slack between the process that rendered and the process
# that executes — not a loophole for backdating, just real-clock slack.
CLOCK_SKEW_TOLERANCE_SECONDS = 30


def compute_token(**fields) -> str:
    """Deterministic short token over the given facts."""
    canonical = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def is_fresh(issued_at: int, max_age_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
             now: int = None) -> bool:
    """True if issued_at is within [now - max_age_seconds, now + skew-tolerance].

    Rejects both stale tokens (replay of an old draft) and implausibly-
    future ones (clock manipulation / fabricated issued_at).
    """
    now = int(now) if now is not None else int(time.time())
    age = now - int(issued_at)
    return -CLOCK_SKEW_TOLERANCE_SECONDS <= age <= max_age_seconds


def advisory_write_token(action: str, doctype: str, name: str, payload: dict,
                          requested_by: str, issued_at: int = None) -> str:
    """Gates every create/update/submit/cancel/delete this skill performs
    — see module docstring for why this is unconditional here, unlike the
    narrower gating in system-admin/fixed-asset-manager's copies.

    payload is folded into the token as-is (sorted-key JSON) so the token
    is bound to the exact drafted field values, not just the doctype/
    action/name shape — a caller can't render one payload and execute a
    different one under the same token.
    """
    if issued_at is None:
        raise ValueError("issued_at is required — pass the render-time epoch seconds.")
    return compute_token(
        kind="advisory_write",
        action=action,
        doctype=doctype,
        name=name or "",
        payload=payload or {},
        requested_by=requested_by or "",
        issued_at=int(issued_at),
    )
