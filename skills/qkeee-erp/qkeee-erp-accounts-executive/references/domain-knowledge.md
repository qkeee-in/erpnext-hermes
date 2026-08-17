# qkeee-erp-accounts-executive domain knowledge

ERP-agnostic in substance — this is what a detail-oriented accounts
executive (junior-to-mid-level accountant working knowledge) knows about
day-to-day AP/AR and tax-compliance operations, independent of which
system executes it. `references/connector-reference.md` and
`scripts/erp_client.py` are the ERPNext-specific layer; ERPNext-specific
asides below point at `references/erpnext-accounting-docs.md` rather than
being baked into the concepts themselves.

## Payment status and the outstanding-amount lifecycle

Every invoice (Sales or Purchase) tracks an outstanding balance that
starts at the invoice total and decreases as payments are recorded and
allocated against it — never edited directly. Payment status is a
derived fact, not something to state from memory: check the invoice's
current outstanding balance and any linked payment records rather than
assuming "invoiced" implies "unpaid" or vice versa. A payment can be
partially allocated (part against this invoice, part left as an advance
for a future one), so "the invoice is paid" and "the payment covers this
invoice in full" are not automatically the same statement — check the
allocated amount, not just that a payment exists referencing the party.

## Journal Entry drafting — advisory-first, always

A Journal Entry is the general-purpose mechanism for postings that don't
belong to a routine sales/purchase/payment workflow — accruals,
adjustments, transfers, write-offs, opening balances. **This skill drafts
JEs but never auto-submits one, regardless of `qkeee_erp.mode`** — see
`SKILL.md`'s non-negotiable. The discipline that makes a draft trustworthy
before a human reviews it:

- **Debits must equal credits before the draft is even shown**, not just
  before submission — `scripts/render_je_draft.py` enforces this in code.
  A JE draft that doesn't balance isn't "close enough," it's wrong; find
  the missing/extra line rather than forcing balance with a plug.
- **Every row needs an account and either a debit or a credit, never
  both** — a row with both filled (or both empty) is a construction bug,
  not a valid entry.
- **State the narration in plain business language**, not just account
  codes — "write off a ₹12,000 bad debt on Invoice ACC-SINV-2026-00042"
  reads; "Dr 12000 Bad Debts / Cr 12000 Debtors" alone doesn't tell a
  reviewer whether the entry is *correct*, only that it's balanced.
- **Never invent an account, cost center, or party reference that wasn't
  confirmed to exist.** A plausible-looking account name that happens to
  be wrong produces a JE that posts to the wrong place — always resolve
  against the actual chart of accounts, not a guess at naming convention.

## AP/AR aging

Outstanding invoices bucketed by how overdue they are (e.g. current,
1-30, 31-60, 61-90, 90+ days past due), computed from each invoice's due
date (which may itself come from a payment-terms schedule rather than a
single flat date — an invoice on 30% advance / 70% on delivery terms has
two due dates, not one). **Reconciliation discipline for this report even
though it's less "self-checking" than a trial balance:** the sum of all
buckets for a party should equal that party's total outstanding balance
from the AR/AP control account — if it doesn't, a payment-terms edge case
or an invoice missing a due date is hiding somewhere, not a report bug to
shrug off.

## 3-way match (PO → Goods Receipt → Invoice)

Confirms that what was ordered, what was actually received, and what's
being billed agree before recommending payment — the core AP control
against paying for goods never received or being overcharged relative to
the PO price. Walk it in order:
1. **PO → Receipt**: did the quantity received match the quantity
   ordered (or a documented partial/over-receipt)?
2. **Receipt → Invoice**: does the invoiced quantity and rate match what
   was actually received, not just what was ordered? (A supplier
   invoicing the full PO quantity when only a partial delivery arrived is
   exactly the discrepancy this match exists to catch.)
3. **PO → Invoice** (price check): does the invoiced rate match the PO
   rate, or is there an unexplained price variance?

Report every discrepancy found, not just the first one — a match report
that stops at the first mismatch and calls it done will miss a second,
independent problem on the same document set. See
`references/erpnext-accounting-docs.md` for the specific ERPNext fields
(`per_received`, `per_billed`, line-item PO references) that make this
walk possible without re-deriving match state by hand.

## Bank reconciliation assist

Matching a bank statement's lines against recorded Payment Entries/JEs
touching the bank account, so the book balance and the real bank balance
agree at a point in time. Every unmatched line is one of exactly two
things — a transaction the books haven't recorded yet (timing, or
missed), or a transaction the bank hasn't cleared yet (outstanding
cheque/deposit in transit) — and the reconciliation should say which, per
line, not just list "unmatched" without a hypothesis. The reconciling
total (book balance + deposits in transit − outstanding payments =
statement balance, or the equivalent framing) is itself the tie-out
check.

## Expense claim review

Checking an expense claim against policy before recommending approve or
flag — category eligibility, receipt presence for amounts above a
threshold, whether it duplicates an already-claimed item. This is
judgment-heavy (policy interpretation), so state the specific policy
point a flag is based on rather than a bare "looks off" — a reviewer
downstream needs to know *why* to act on the flag.

**This skill has no built-in expense policy of its own — none is baked
into this file, and none should be.** Every org's expense policy differs
(category limits, receipt thresholds, approval chains), and inventing a
plausible-sounding default would produce confident-looking recommendations
grounded in a policy that isn't actually the org's. Before reviewing a
claim: if the org's policy hasn't already been stated in the current
session, ask for it explicitly (a summary is enough — exact category
limits and receipt-threshold rules are what matters for a flag decision).
Cite the specific stated rule when flagging, never a generic "typically
expense policies require...". If no policy is available and the user
wants a review anyway, say plainly that the recommendation is based on
general expense-audit judgment (duplicates, missing receipts, unusually
round amounts) rather than the org's actual rules — don't let a policy-
free review read as if it were policy-grounded.

## Tax mechanics — jurisdiction-general framing, India specifics called out

**TDS / withholding tax** is a general accounting concept (withhold a
portion of a payment and remit it to the tax authority on the payee's
behalf) that ERPNext implements natively via the Tax Withholding Category
mechanism — **confirmed live against `<erp-instance>` to be core ERPNext
functionality, not gated behind any India-specific app** (see
`references/erpnext-accounting-docs.md`). The India-specific layer is the
*rate/section table* (e.g. Section 194J for professional services), which
is configuration on top of a jurisdiction-agnostic mechanism, not a
different mechanism.

**GST (or the equivalent VAT/GST regime in another jurisdiction)** — the
return-filing formats (GSTR-1/3B), e-invoicing (IRN generation), and
e-way bill are genuinely jurisdiction-specific and, on ERPNext, require
the dedicated India Compliance app rather than being core functionality
— see `references/erpnext-accounting-docs.md` for why `tax_id` alone
(present on stock ERPNext) isn't the same as a validated GSTIN field.

**Standing disclaimer for every tax-related output this skill
produces:** state plainly that the output assists, doesn't replace,
verification against current regulation — rates, thresholds, and formats
change, and this skill's knowledge reflects what was confirmed at build
time (2026-08-10), not a live regulatory feed. Government portals
(`gst.gov.in`, `einvoice1.gst.gov.in`, `ewaybillgst.gov.in`, the Income
Tax e-filing portal) are the ground-truth authority, never this skill's
own memory of the rule.

## Regional/regulatory scope note

This skill owns the operational/compliance lens on tax mechanics (does
this specific transaction need TDS withheld, is this invoice
e-invoicing-eligible, what does this GST return period's data look like)
— `qkeee-erp-mis-analyst` owns the management/analytical lens on the same
underlying GL (P&L, balance sheet, variance) and explicitly routes
statutory questions back here rather than answering them itself.
