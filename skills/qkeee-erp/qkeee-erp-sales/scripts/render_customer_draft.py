#!/usr/bin/env python3
"""
Customer onboarding draft renderer for qkeee-erp-sales.

ERPNext's own hard-mandatory fields on Customer are only customer_name +
customer_type (confirmed live against <erp-instance>: a Customer with
nothing else set creates cleanly). This skill's KYC-completeness bar is
stricter — refuses to mark a draft "ready" unless customer_name,
customer_type, customer_group, territory, and at least one primary-contact
channel (email or mobile) are present and (for extraction-derived fields)
above MIN_CONFIDENCE. Incomplete extractions are flagged back to the user,
never silently filled with a placeholder.

Live-confirmed, non-obvious shape (<erp-instance>): Customer's
own mobile_no/email_id fields are fetch_from=customer_primary_contact.*
(Read Only in the UI sense) — they are NOT settable directly on the
Customer record itself, and customer_primary_contact does NOT get set
automatically just because a Contact links to the Customer via its own
links table. A KYC-complete customer with working contact info requires
THREE separate writes, in order:
  1. create Contact (first_name, email_ids[], phone_nos[],
     links=[{"link_doctype": "Customer", "link_name": <customer name>}])
  2. create Customer (or it must already exist)
  3. update Customer.customer_primary_contact = <Contact name> — only
     this step populates Customer.mobile_no / Customer.email_id
Contact is autonamed "<first_name>-<link_name>" when linked at create
time. This renderer stages all three payloads; SKILL.md's Execute step
must run them in this order, not in parallel.

Also sanity-checks contact_email/contact_mobile look like a real email/
phone number (not RFC-strict — just enough to catch "n/a", a bare name,
or a garbled extraction slipping through as a "reachable" contact at
high confidence).

Trust boundaries, stated plainly: `execute_order` is advisory text for
the calling agent to follow — this function has no way to enforce that
the three mutate_resource() calls actually happen in order, since it
never calls ERPNext itself. Likewise, `confidence` is entirely
self-reported by the caller; nothing here verifies its provenance. A
"ready" result means the inputs are internally consistent, not that the
calling agent behaved correctly upstream.
"""

MIN_CONFIDENCE = 0.75  # conservative default, not a validated figure derived from real extraction-accuracy data

REQUIRED_CUSTOMER_FIELDS = ["customer_name", "customer_type", "customer_group", "territory"]


def _looks_like_email(value: str) -> bool:
    # Cheap sanity check, not RFC 5322 validation — catches "n/a",
    # "unknown", a bare name, etc. slipping through as a "reachable"
    # contact channel at high confidence. ERPNext itself does its own
    # stricter validation at create time; this exists to fail earlier,
    # with a clearer message, not to replace that.
    return isinstance(value, str) and "@" in value and "." in value.split("@")[-1]


def _looks_like_mobile(value: str) -> bool:
    digit_count = sum(c.isdigit() for c in str(value))
    return digit_count >= 7


def render_customer_draft(fields: dict, confidence: dict = None) -> dict:
    """Build a staged Customer (+ optional Contact) draft.

    `fields` — dict of Customer field values, plus optional
      "contact_first_name", "contact_email", "contact_mobile" for the
      linked-Contact leg.
    `confidence` — optional dict of field_name -> confidence (0-1), for
      extraction-sourced fields. Fields absent from this dict are treated
      as human-provided (full confidence).

    Returns {"ready": bool, "issues": [...], "customer_payload": {...},
             "contact_payload": {...} | None, "execute_order": [...]}.
    """
    confidence = confidence or {}
    issues = []

    for field in REQUIRED_CUSTOMER_FIELDS:
        value = fields.get(field)
        if not value:
            issues.append(f"Missing required field: {field}")
            continue
        conf = confidence.get(field, 1.0)
        if conf < MIN_CONFIDENCE:
            issues.append(f"Low-confidence field: {field} (confidence={conf:.2f}, needs review)")

    has_email = bool(fields.get("contact_email"))
    has_mobile = bool(fields.get("contact_mobile"))
    if not has_email and not has_mobile:
        issues.append("Missing primary contact channel: need at least contact_email or contact_mobile")
    else:
        for field in ("contact_email", "contact_mobile"):
            if fields.get(field) and confidence.get(field, 1.0) < MIN_CONFIDENCE:
                issues.append(f"Low-confidence field: {field} (confidence={confidence[field]:.2f}, needs review)")
        if has_email and not _looks_like_email(fields["contact_email"]):
            issues.append(f"contact_email {fields['contact_email']!r} doesn't look like a real email address — needs review.")
        if has_mobile and not _looks_like_mobile(fields["contact_mobile"]):
            issues.append(f"contact_mobile {fields['contact_mobile']!r} doesn't look like a real phone number — needs review.")

    customer_payload = {
        "customer_name": fields.get("customer_name"),
        "customer_type": fields.get("customer_type"),
        "customer_group": fields.get("customer_group"),
        "territory": fields.get("territory"),
    }
    # Optional fields are still extraction-sourced when present — a
    # garbled tax_id written silently into a KYC-sensitive record is
    # exactly the failure mode this skill exists to prevent, so the same
    # confidence gate applies here, not just to the required set.
    for optional in ("default_currency", "tax_id", "default_price_list", "payment_terms"):
        if fields.get(optional):
            conf = confidence.get(optional, 1.0)
            if conf < MIN_CONFIDENCE:
                issues.append(f"Low-confidence field: {optional} (confidence={conf:.2f}, needs review)")
            customer_payload[optional] = fields[optional]

    contact_payload = None
    if has_email or has_mobile:
        contact_payload = {
            "first_name": fields.get("contact_first_name") or fields.get("customer_name"),
            "email_ids": [{"email_id": fields["contact_email"], "is_primary": 1}] if has_email else [],
            "phone_nos": [{"phone": fields["contact_mobile"], "is_primary_mobile_no": 1}] if has_mobile else [],
            # Deliberately NOT pre-filled with fields["customer_name"]: this
            # renderer runs before Customer create, so it can't know the
            # Customer's real assigned name (naming rule on this instance
            # happens to echo customer_name verbatim, but that's a per-org
            # Selling Settings choice, not guaranteed elsewhere — see
            # erpnext-selling-docs.md). Left as an explicit, obviously-fake
            # placeholder string so a caller who forgets to substitute it
            # sends a payload that's visibly wrong on inspection — NOT a
            # KeyError (the key is present) and NOT independently confirmed
            # to fail server-side (Dynamic Link target-existence validation
            # against this literal string was not live-tested in this
            # build). Don't rely on ERPNext to catch this — the calling
            # skill (SKILL.md step 5) must substitute the real Customer
            # name before calling mutate Contact create.
            "links": [{"link_doctype": "Customer", "link_name": "<FILL IN: created Customer's real name>"}],
        }

    return {
        "ready": len(issues) == 0,
        "issues": issues,
        "customer_payload": customer_payload,
        "contact_payload": contact_payload,
        "execute_order": (
            ["create Customer", "create Contact (link_name = created Customer's real name)",
             "update Customer.customer_primary_contact = created Contact's name"]
            if contact_payload else ["create Customer"]
        ),
    }
