---
name: qkeee-erp-doc-extraction
description: "Extracts document fields into a staged review report."
metadata:
  hermes:
    tags: [ERPNext, Document-Extraction, Utility, OCR, Staged-Review]
    related_skills: [qkeee-erp-hr-associate, qkeee-erp-procurement, qkeee-erp-accounts-executive]
---

# qkeee-erp-doc-extraction

Utility skill, not a domain persona. Turns attached documents — including
scanned/photographed images — or a shared URL (LinkedIn profile, hosted
document link) into structured fields shaped for a target ERPNext doctype
(Supplier, Purchase Invoice, Job Applicant, Employee), then stops — it
hands back a staged report, never a written record.

**Scope note:** the module plan scopes this skill's inputs to attached
PDFs/DOCX/XLSX/images. Scanned-image handling is a direct extension of
that (still a file, just image-typed). URL-based extraction is a
materially different capability — fetching arbitrary external web
content — added on top of the plan's original scope; flagged here rather
than presented as if the plan always covered it.

## When to Use

Use when the user attaches a document or shares a link during
HR/Procurement/Accounts work (or any `qkeee-erp-*` persona session), or
asks to pull structured fields out of a PDF/resume/invoice/business
card/scanned image/profile URL, ERP-related or not.

## Prerequisites

No ERPNext credentials or environment configuration needed — this
skill ships no connector at all (see Pitfalls). Only requirement is a
harness that can read the attached file type or fetch the given URL;
see Procedure step 1 for the tool-discovery order.

## Pitfalls

**Never create or update an ERPNext record directly from extraction
output.** Output always lands as a staged, human-reviewable report first —
this holds regardless of any calling skill's `qkeee_erp.mode`. This isn't
a self-imposed restraint on a capability the skill has and chooses not to
use: this skill ships **no ERPNext connector at all** — no copy of
`erp_client.py`, no `mutate_resource()`, nothing that can reach ERPNext's
write endpoints. Writing to ERPNext from here isn't refrained from, it's
structurally impossible. **Low-confidence or not-found fields must be
explicitly flagged, never silently guessed or filled with a placeholder.**

This is the one skill in the `qkeee-erp` library not gated by
`qkeee_erp.mode` — its safety property is structural, not config-driven.

**URL-based extraction never bypasses a login wall or paywall.** Fetch
only what's publicly retrievable via the harness's normal web-fetch
behavior. Never attempt to authenticate as the user, use stored
credentials/cookies, or work around a site's access controls — if a page
comes back blocked, partial, or login-gated (LinkedIn profile pages
commonly are, to non-logged-in fetchers), say so plainly and stage
whatever was actually retrieved as low-confidence rather than padding it
with assumptions.

## Procedure

1. **Discover harness file-reading and web-fetch tools before using
   bundled logic.** Concretely: if the harness exposes a tool-search or
   tool-listing mechanism (in Claude Code, `ToolSearch`), use it to check
   for `document-skills:pdf`, `document-skills:docx`,
   `document-skills:xlsx`, native multimodal image reading, and a
   URL-fetch-capable tool (e.g. `WebFetch`), and prefer those over any
   bundled parsing. This skill's own value-add is the
   field-mapping/confidence layer on top, not raw file/page fetching. If
   the harness exposes no such mechanism at all (not "I didn't check" —
   actually absent), degrade gracefully: use whatever's available (native
   multimodal reading covers PDF/DOCX/image directly in most Claude-based
   harnesses; `WebFetch` or equivalent covers URLs) and don't hard-fail
   just because discovery itself wasn't possible.
2. **Scanned/photographed images go through the same multimodal reading
   path as clean files** — no separate OCR dependency. Treat image
   quality as a first-class confidence signal: blur, glare, skew,
   handwriting, or a cropped edge should push affected fields to
   `medium`/`low` confidence rather than reported as if read from a clean
   PDF. If a load-bearing field (e.g. `tax_id`, bank account number) is
   illegible, say so explicitly and suggest the user resend a clearer
   capture rather than guessing.
3. **URLs are fetched, not assumed.** When given a link instead of an
   attachment (a LinkedIn profile, a hosted invoice/document link), fetch
   it via the discovered web-fetch tool and extract from the returned
   content only. Public profile/listing pages are often partial for a
   non-authenticated fetch (LinkedIn in particular routinely serves a
   stripped-down view or a login wall to unauthenticated requests).
   Concrete signals the fetch was a thin/blocked page rather than a real
   profile — any one of these is enough to mark every field pulled from
   it `confidence: "low"` and say so in `notes`: the fetched text is
   under ~500 characters of actual content; it contains literal
   sign-in/paywall strings ("Sign in", "Join LinkedIn", "to see more",
   "Log in to view"); or none of the expected section markers (name
   header, Experience, Education) are present at all. Don't wait for
   certainty — any one signal is sufficient, don't require all three.
4. **Identify the target doctype shape** — Supplier, Invoice/Bill (+ line
   items), or Job Applicant/Employee — from context (which persona invoked
   this, or what the user asked for). See
   `references/field-mappings.md` for the field list and typical source
   location per doctype; treat it as a starting point, not verified ground
   truth (flagged there as build-time-only, unconfirmed against a live
   ERPNext instance).
5. **Extract, and rate every field's confidence** as `high` / `medium` /
   `low` per the convention in `references/field-mappings.md`. A field
   that isn't in the document/page at all is `value: null` — not omitted,
   not guessed, regardless of confidence. A field that's genuinely blank
   in the source (e.g. an empty optional box on a form) is `value: ""`,
   which is a different, present-but-empty signal, not the same as
   `null`. For a repeating line item (invoice line items, education
   entries), tag each field with a `row` identifier (e.g. `"items[0]"`)
   so they render as one grouped line item instead of colliding into a
   flat table — see `scripts/render_staged_report.py`'s docstring.
6. **Render the staged report via `scripts/render_staged_report.py`.**
   Always through the script, not reproduced inline — the script is the
   only place the confidence/value-key requirements are actually
   enforced, and an inline reproduction has no such enforcement. It
   refuses to render if any field is missing a confidence rating or a
   `value` key, which is intentional: don't work around that by inventing
   one. For URL sources, set `source` to the URL (or URL + section)
   rather than a page/table reference. For an invoice's total-reconciliation
   self-check (line items + tax lines vs. `grand_total`), record the
   result as its own field — `field: "reconciliation_check"`, `value`
   stating what was compared and whether it tied out (e.g. `"items+tax
   132.50 vs grand_total 132.50 - ties out"` or a mismatch amount),
   `confidence: "high"` if it ties out or `"low"` if it doesn't — so the
   check result is visible in the report itself, not just something the
   agent is trusted to remember to mention.
7. **Hand the staged report back** to the user, or to the calling persona
   skill if invoked inline (e.g. from `qkeee-erp-procurement` mid-supplier-
   onboarding). The receiving skill is responsible for its own Confirm →
   Execute steps against ERPNext — this skill's job ends at the staged
   report.
8. **Standalone use is fine.** Nothing about this skill assumes an
   ERPNext context — pulling structured fields out of a PDF, scanned
   image, or shared URL for a purpose unrelated to ERPNext is a
   legitimate, unblocked use.

## Quick Reference

| Capability | Outcome | Notes |
| --- | --- | --- |
| Supplier detail extraction | Supplier onboarding fields + confidence, staged | KYC docs/onboarding forms/business cards, incl. scanned/photographed; see field-mappings.md |
| Invoice + line-item extraction | Invoice header + line items + confidence, staged | Line items grouped via `row` (e.g. `items[0]`); self-check totals against `grand_total` and record the result as a `reconciliation_check` field, not just prose in `notes` |
| Resume/employee detail extraction | Candidate/employee fields + confidence, staged | From a resume file, a scanned copy, or a shared profile URL; never infer compensation/notice-period fields not literally present |
| Scanned/photographed image extraction | Same field sets as above, sourced from an image capture rather than a clean file | No OCR dependency — routed through native multimodal image reading; image quality issues (blur/glare/skew) push affected fields to lower confidence |
| URL-based extraction | Structured fields pulled from a shared link (LinkedIn profile, hosted invoice/document page) | Public/unauthenticated fetch only — never bypasses login/paywall; partial/blocked fetches are flagged, not padded |
| Harness file-tool + web-fetch discovery | Prefer existing PDF/DOCX/XLSX/image/URL-fetch tools over bundled parsing | Attempt-then-degrade — never hard-fail if discovery isn't supported |

## Verification

Every field must carry an explicit `value` (even if `null`) and a
confidence rating before the report renders — `render_staged_report.py`
refuses otherwise, don't work around that by inventing one. For an
invoice, confirm the `reconciliation_check` field is present and states
whether line items + tax tied out against `grand_total`.

## Files

- `references/field-mappings.md` — target-doctype field lists, typical
  source location per field, and the confidence-rating convention.
- `scripts/render_staged_report.py` — formats extraction output as a
  Markdown staged report; enforces that every field carries a confidence
  rating and an explicit `value` (never both omitted), groups repeating
  line items via `row` into their own sub-tables instead of one flat
  table, and surfaces low-confidence/not-found fields up top rather than
  burying them in a table row.

## Relationships

Consumed by `qkeee-erp-hr-associate` (resumes), `qkeee-erp-procurement`
(supplier KYC docs), `qkeee-erp-accounts-executive` (vendor invoices, bank
statements). Each of those skills points the user here (or invokes this
inline) when a relevant file is attached, and degrades to manual data
entry if this skill isn't installed — no hard dependency either direction.
