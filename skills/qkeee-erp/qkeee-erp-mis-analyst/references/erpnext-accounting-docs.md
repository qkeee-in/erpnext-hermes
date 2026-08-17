# ERPNext accounting documentation map

Curated pointers into `docs.frappe.io`'s ERPNext accounting documentation,
fetched and summarized while building this skill (2026-08-10). This is a
build-time research aid *and* a runtime reference — when you (the agent)
are unsure how a concept behaves, which report to run, or what a filter
means, fetch the relevant URL below directly rather than guessing from
this summary alone. **Summaries here can drift from the live docs; the
URL is the source of truth, this file is a starting index into it.**

If the harness has a web-fetch tool available (e.g. `WebFetch` in Claude
Code), use it against these URLs before answering an accounting-mechanics
question you're not certain of — this is cheap and the whole point of
listing them here rather than relying on memorized/inferred ERPNext
behavior, which is exactly the kind of confident-but-wrong answer this
persona is built to avoid.

## Core concepts

| Topic | URL | What's there |
| --- | --- | --- |
| Accounting overview | `https://docs.frappe.io/erpnext/accounting/introduction` | High-level orientation; thin on its own, better read alongside the pages below |
| Chart of Accounts | `https://docs.frappe.io/erpnext/chart-of-accounts` | Group vs Ledger accounts, root types (Asset/Liability/Equity/Income/Expense), Account Type field, multi-company trees, control-account guidance |
| Journal Entry | `https://docs.frappe.io/erpnext/journal-entry` | Debit/credit row mechanics, the full Entry Type list (Opening Entry, Exchange Rate Revaluation, Depreciation Entry, Write Off, Contra, Inter Company, ...), multi-currency handling, document-reference rules |
| Opening Balance in Accounts | `https://docs.frappe.io/erpnext/opening-balance` | Opening Journal Entry (`Is Opening = Yes`) for Balance Sheet accounts, Opening Invoice Creation Tool for AR/AP, Temporary Opening account should net to zero, no hiding differences in retained earnings/suspense |
| Fiscal Year | `https://docs.frappe.io/erpnext/fiscal-year` | Fiscal year definition and auto-creation behavior near year-end |
| Period Closing Voucher | `https://docs.erpnext.com/docs/user/manual/en/period-closing-voucher` | Rolls P&L balances into a retained-earnings-type account (e.g. "Reserves and Surplus") at fiscal year close — the mechanical link between P&L and Balance Sheet across years |
| Accounting Dimensions | `https://docs.frappe.io/erpnext/accounting-dimensions` | Custom dimensions beyond the default Cost Center/Project, "Mandatory for P&L" vs "Mandatory for Balance Sheet" checkboxes, dimension filters restricting which values apply to which accounts |
| Finance Book | `https://docs.frappe.io/erpnext/finance-book` | Parallel accounting views (e.g. statutory vs management depreciation) — **critical reconciliation gotcha, see below** |
| Multi Currency Accounting | `https://docs.frappe.io/erpnext/multi-currency-accounting` | Account currency vs Company currency, GL Entry carries both, Exchange Rate Revaluation for unrealized FX movement |
| Exchange Rate Revaluation | `https://docs.frappe.io/erpnext/exchange-rate-revaluation` | The master used to adjust GL balances for exchange-rate changes on open foreign-currency balances |

## Reports (map capability → exact `report_name` for `run_query_report()`)

| Topic | URL | Report name(s) to pass to `scripts/erp_client.py report` |
| --- | --- | --- |
| Accounting Reports index | `https://docs.frappe.io/erpnext/accounting-reports` | Index of every report below, with filters/columns described |
| General Ledger | (same page) | `"General Ledger"` |
| Trial Balance | (same page) | `"Trial Balance"`; party-scoped variant: `"Party-wise Trial Balance"` |
| Balance Sheet | (same page) | `"Balance Sheet"` |
| Profit and Loss | (same page) | `"Profit and Loss Statement"` |
| Cash Flow | (same page) | `"Cash Flow Statement"` |
| Consolidated statements | (same page) | Consolidated Balance Sheet / P&L / Cash Flow (group companies) |
| Financial Ratios | (same page) | `"Financial Ratios"` — liquidity/profitability/leverage/turnover ratios |
| AR/AP aging | (same page) | `"Accounts Receivable"` / `"Accounts Payable"` |
| Budget Variance | (same page) | `"Budget Variance Report"` — built-in support for this skill's Variance Analysis capability at cost-center level |

## The Finance Book reconciliation gotcha (read before building any BS/P&L/GL report)

From the Finance Book page, verbatim points that matter directly for this
skill's "numbers must tie out" non-negotiable:

- An entry posted against a specific Finance Book belongs only to that
  book; an entry with **no** Finance Book set is "common" and appears in
  every Finance Book's report view by default.
- General Ledger's **"Include Default FB Entries"** filter is on by
  default; switching to a non-default Finance Book requires clearing it
  first, or ERPNext refuses the report.
- **"A report filtered to one Finance Book cannot be compared reliably
  with an unfiltered report or a report using a different setting."**

**Implication for this skill:** before declaring a reconciliation check
between two report pulls (e.g. drill-down sum vs a Trial Balance total),
confirm both were run with the *same* Finance Book filter. A tie-out
failure that's actually just a Finance Book filter mismatch between two
queries is a false anomaly — check this before reporting a discrepancy as
real. If the org uses multiple Finance Books, state which one a report
reflects in its title/notes, every time.

## The multi-currency reconciliation note

From the Multi Currency Accounting page: financial statements are
grounded in **Company currency** — GL Entry carries both the
account-currency amount and the Company-currency equivalent. When
reconciling, compare Company-currency figures unless a report is
explicitly presenting account-currency values (e.g. an AR/AP report
filtered to one customer's invoice currency). Exchange Rate Revaluation
entries are how unrealized FX movement enters the GL for a still-open
foreign balance — a discrepancy between two periods' Company-currency
balance for an unchanged foreign amount is not necessarily an anomaly,
it may be a legitimate revaluation; check for a revaluation-type Journal
Entry in the period before flagging it as unexplained.

## Known coverage gaps

This map lists `report_name` values for Consolidated Financial
Statements and Financial Ratios but doesn't document their exact
computation (ratio formulas, group-company consolidation rules) or
AR/AP report mechanics in depth — treat those as needing direct
doc/API verification at request time, not as pre-vetted the way the
Finance Book and multi-currency notes above are.

## Regional/regulatory reports out of scope here

GST India reports (GSTR-1, GSTR-2) and other India Compliance content
appear on the Accounting Reports index page but belong to
`qkeee-erp-accounts-executive`'s domain (operational/compliance lens),
consistent with this skill's `references/domain-knowledge.md` scope note.
Not indexed further here.

## Staleness note

Fetched 2026-08-10 against the current `docs.frappe.io` content, which is
largely version-unpinned (some search results also surfaced
version-pinned mirrors like `/erpnext/v13/...` or `/erpnext/v14/...` —
prefer the unversioned URLs above unless the target org is confirmed on
an older ERPNext version). Doctype field lists and report filter schemas
should still be confirmed against the target instance directly (`GET
/api/resource/DocType/<DocType Name>`, or opening the report once in the
ERPNext UI to see its filter panel) — this file gives concept-level
grounding, not a guarantee of an exact field/filter name for any given
org's customized instance.
