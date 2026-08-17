# Target-doctype field mappings

**Verified against a live instance: ERPNext v15.112.0 / Frappe v15.112.0
(<erp-instance>, checked 2026-08-10)**, apps installed: `frappe`,
`erpnext`, `hrms` (Frappe HR), `crm` — **no India Compliance app**
installed on this instance, so GSTIN/e-invoicing/e-way-bill fields are
not present as dedicated fields here; `tax_id` is the generic Data field
used for any tax identifier. Field lists below reflect that instance's
DocType metadata (pulled via `GET /api/resource/DocType/<name>`, which
returns the live `fields` schema including `reqd`/mandatory flags). A
different org's instance may have customized fields (custom fields,
different mandatory flags, India Compliance installed) — re-verify the
same way (`GET /api/resource/DocType/<DocType>` as a System Manager) if a
field lookup here doesn't match what you observe. Fields marked **REQ**
below are mandatory (`reqd: 1`) on the checked instance.

## Supplier (→ `qkeee-erp-procurement` supplier onboarding)

From onboarding forms / KYC documents / business cards. Mandatory on
Supplier: `supplier_name` (Data), `supplier_type` (Select: Company /
Individual / Partnership).

| Field | Type | Mandatory | Typical source in doc |
| --- | --- | --- | --- |
| `supplier_name` | Data | **REQ** | Letterhead, form header |
| `supplier_type` | Select (Company/Individual/Partnership) | **REQ** | Company vs Individual — form checkbox |
| `supplier_group` | Link → Supplier Group | no | Rarely in-doc — usually inferred/asked, flag low-confidence if guessed |
| `country` | Link → Country | no | Address block |
| `tax_id` | Data | no | Form field, often printed near address — GSTIN/PAN/VAT number depending on region; this field is generic, not GST-specific, on an instance without India Compliance |
| `tax_category` | Link → Tax Category | no | Rarely on the source doc — usually an org-side lookup, not extracted |
| `website` | Data | no | Letterhead |
| `mobile_no` / `email_id` | Read Only (pulled from linked Contact, not directly settable on create) | no | Contact section — stage as data for the consuming skill to create/link a Contact record, since these are read-only on Supplier itself |
| Bank details (`default_bank_account` is a Link, not raw account/IFSC fields) | Link → Bank Account | no | Often a separate KYC/bank-proof page — ERPNext models this as a linked Bank Account record, not inline fields on Supplier, so stage bank details as their own field group for the consuming skill to create a Bank Account record against |

**Never fill `tax_id` or bank-detail fields with a placeholder if not
found — leave `value: null`, `confidence: "low"`.** These are exactly the
mandatory-KYC fields `qkeee-erp-procurement`'s non-negotiable exists to
protect. Note `mobile_no`/`email_id` are **Read Only** on the Supplier
doctype itself (sourced from the linked primary Contact) — flag this in
the staged report so the consuming skill knows it must create/update a
Contact, not set these fields directly on Supplier.

## Invoice + line items (→ `qkeee-erp-accounts-executive` invoice/bill entry, 3-way match)

Doctype: **Purchase Invoice**. Mandatory header fields: `naming_series`,
`supplier` (Link → Supplier), `posting_date`, `items` (child table),
`credit_to` (Link → Account). Line items live in the **Purchase Invoice
Item** child table; mandatory there: `item_name`, `qty`, `uom`,
`conversion_factor`, `stock_qty`, `rate`, `amount`, `base_rate`,
`base_amount`.

| Field | Type | Mandatory | Typical source in doc |
| --- | --- | --- | --- |
| `naming_series` | Select | **REQ** | Not present on the source document — an ERPNext internal numbering-scheme selector, not something a vendor invoice would ever state; the consuming skill picks/defaults this, don't attempt to extract it |
| `supplier` | Link → Supplier | **REQ** | Header — must resolve to an existing Supplier record; stage the vendor name as extracted and flag low-confidence if no exact Supplier match exists yet |
| `posting_date` | Date | **REQ** | Header, near date — defaults to today if not extracted; don't silently default a value the doc doesn't state without flagging it |
| `bill_no` | Data | no | Vendor's own invoice number — header, near date |
| `bill_date` | Date | no | Header |
| `credit_to` | Link → Account | **REQ** | Not present on the source document — org's AP account, resolved by the consuming skill, not extracted |

Line items — each field tagged with a `row` identifier (`"items[0]"`,
`"items[1]"`, ...) per line so `render_staged_report.py` groups them
instead of colliding into one flat table:

| Field | Type | Mandatory | Typical source in doc |
| --- | --- | --- | --- |
| `items[].item_code` | Link → Item | no | Line-item table — won't match ERPNext's internal Item code without a lookup; extract `item_name`/description instead and flag `item_code` low-confidence pending a query against existing Items |
| `items[].item_name` | Data | **REQ** (child) | Line-item table |
| `items[].qty` | Float | **REQ** (child) | Line-item table |
| `items[].uom` | Link → UOM | **REQ** (child) | Line-item table — often absent on informal invoices; flag low-confidence and default-suggest rather than silently pick a UOM |
| `items[].rate` | Currency | **REQ** (child) | Line-item table |
| `items[].amount` | Currency | **REQ** (child) | Line-item table — self-check: should equal `qty * rate` |
| Tax lines (GST/VAT breakup) | via `taxes` child table (Purchase Taxes and Charges) | no | Usually a subtotal block — treat each tax line as its own field, not folded into one number |
| `grand_total` | Currency | no (system-computed on submit) | Footer |

**Reconciliation self-check is a field, not just a prose reminder.**
Sum the line `amount`s plus tax lines and compare to `grand_total`;
record the result as its own header field —
`{"field": "reconciliation_check", "value": "items+tax 132.50 vs
grand_total 132.50 - ties out", "confidence": "high"}` (or `"confidence":
"low"` with the mismatch amount stated in `value` if it doesn't
reconcile). This makes the check visible in the rendered report itself
instead of relying on the agent to separately remember and mention it.

## Resume / candidate details (→ `qkeee-erp-hr-associate` Job Applicant)

Doctype: **Job Applicant**. Mandatory: `applicant_name`, `email_id`,
`status` (defaults to "Open" — a sensible default, not something to
extract from a resume).

| Field | Type | Mandatory | Typical source in doc |
| --- | --- | --- | --- |
| `applicant_name` | Data | **REQ** | Header |
| `email_id` | Data (Email) | **REQ** | Header/contact block |
| `phone_number` | Data (Phone) | no | Header/contact block |
| `country` | Link → Country | no | Address, if present |
| `designation` | Link → Designation | no | Not usually literal on a resume — the role *applied for*, not the candidate's current title; don't conflate with current/most-recent role below |
| `resume_attachment` | Attach | no | The file itself — the consuming skill attaches the source file, this skill doesn't need to extract this field |
| `lower_range` / `upper_range` | Currency | no | Compensation expectation — **do not infer**; only set if a number is literally stated as expected/desired compensation, per the no-guessing posture below |
| Current/most-recent role + employer | *(no direct Job Applicant field — informational only, useful in `notes` or if the receiving skill separately creates an Employee/Experience record)* | — | Experience section, top entry |
| Total years of experience | *(no direct field)* | — | Often not stated explicitly — if inferred by summing date ranges, mark `confidence: "medium"` and note the calculation in `notes`, never present an inferred number as high-confidence |
| Education | *(maps to Employee Education child table if/when an Employee record is later created from a hired applicant, not to Job Applicant directly)* | — | Education section |

Do not infer or estimate compensation expectations, notice period, or any
field not literally present in the document — leave unset rather than
guess, per HR's PII/no-guessing posture in the module plan.

## Employee (→ `qkeee-erp-hr-associate` employee onboarding, once a Job Applicant is hired)

Doctype: **Employee**. Distinct from Job Applicant above — this is the
record created *after* an offer is accepted, not at resume-intake time.
Mandatory: `first_name`, `gender` (Link → Gender), `date_of_birth`,
`date_of_joining`, `status` (Select, defaults "Active"), `company`
(Link → Company).

| Field | Type | Mandatory | Typical source in doc |
| --- | --- | --- | --- |
| `first_name` / `last_name` | Data | `first_name` **REQ** | Resume/offer paperwork header |
| `gender` | Link → Gender | **REQ** | Rarely stated explicitly on a resume — usually collected separately during onboarding, not extracted from the source document; flag low-confidence if inferred rather than stated |
| `date_of_birth` | Date | **REQ** | ID document, not typically on a resume — usually comes from a separate KYC doc, not the resume itself |
| `date_of_joining` | Date | **REQ** | Offer letter / onboarding form, not the resume |
| `company` | Link → Company | **REQ** | Not on the source document — org context, resolved by the consuming skill |
| `personal_email` / `cell_number` | Data | no | Contact block |
| `education` | Table → Employee Education (`school_univ`, `qualification`, `level`, `year_of_passing`) | no | Education section — tag each entry with a `row` (e.g. `"education[0]"`) the same way invoice line items are grouped |
| `bank_name` / `bank_ac_no` | Data | no | Bank-proof document, not a resume — same low-confidence-if-absent treatment as Supplier bank details above |

Most Employee-only fields (DOB, date of joining, company, bank details)
won't be present on a resume at all — that's expected, not a sign of a
missed extraction. Don't infer them from a resume's contents; leave
`value: null` and let the consuming skill collect them through the
onboarding flow.

## Scanned/photographed sources (applies across all four doctypes above)

Same field targets, different reliability profile. Route through native
multimodal image reading, not a bundled OCR dependency. Specific to image
captures:

- **Skew/rotation/crop** — a field cut off at the image edge is
  `value: null`, `confidence: "low"`, not a guess at what's missing.
- **Glare/blur over a specific region** (common over printed amounts or
  signatures) — if one field is illegible but the rest of the page is
  clear, don't downgrade the whole document, just that field.
- **Handwritten fields** (common on KYC forms, expense claims) — cap at
  `confidence: "medium"` even when legible, since handwriting
  misreads are a known failure mode; call this out in `notes` for
  anything load-bearing (amounts, account numbers, tax IDs).

## URL sources (LinkedIn profile → resume/candidate fields; hosted document/invoice links)

- Fetch only what a normal unauthenticated request returns. **Never**
  attempt login, use stored session cookies, or otherwise access
  content the fetch wouldn't reach on its own.
- LinkedIn profile pages served to unauthenticated fetchers are commonly
  truncated (a summary/preview rather than the full profile) or replaced
  with a login prompt entirely. Concrete signals of this (any one is
  enough — see SKILL.md step 3 for the same list): fetched text under
  ~500 characters; literal sign-in/paywall strings ("Sign in", "Join
  LinkedIn", "to see more", "Log in to view"); or none of the expected
  section markers (name header, Experience, Education) present. Any one
  signal → say so in `notes` and mark every field pulled from it
  `confidence: "low"` — don't present a thin scrape as equivalent to a
  resume file.
- Map whatever sections *are* present the same way as the resume table
  above (name/contact/current role/education/skills) — a URL source
  doesn't get its own field list, just a different, generally noisier,
  reliability profile.
- For a hosted invoice/document link, apply the Invoice or Supplier field
  tables above the same way; `source` for each field should be the URL
  itself (plus a section anchor/heading if the page is long enough to
  need one).

## Confidence convention (applies to all sources above)

- **high** — value read verbatim from an unambiguous location in the doc.
- **medium** — value present but required light inference (e.g. summing
  date ranges, resolving an abbreviation).
- **low** — value found but illegible, contradicted elsewhere in the doc,
  or otherwise shaky. Not the same thing as "not found" (below) — a field
  can be present and low-confidence at the same time.

`value` carries a separate, orthogonal signal from confidence — don't
conflate the two:

- `value: null` — the field was not found in the source at all.
- `value: ""` — the field was found and is genuinely blank (e.g. an
  empty optional box on a form). This is a real, present-and-empty
  result, distinct from "not found" — pair it with whatever confidence
  is warranted (often `"high"`, since finding a field genuinely blank can
  be just as unambiguous as finding it filled in).
- Any other value — set `confidence` to whatever the extraction actually
  warrants; never upgrade to `"high"` just because a value exists. If a
  best-guess value is shown *as a guess* rather than left `null`, say so
  plainly in that field's `source` or in `notes`, never silently.

Every field in a staged report must carry one of these three confidence
levels, plus an explicit `value` key (even when that value is `null`) —
see `scripts/render_staged_report.py`, which refuses to render a report
missing either.
