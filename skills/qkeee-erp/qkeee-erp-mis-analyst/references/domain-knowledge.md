# qkeee-erp-mis-analyst domain knowledge

ERP-agnostic. This is what a Chartered-Accountant-level MIS/reporting
analyst knows about general ledger structure and financial reporting
construction — independent of which system executes the query. Only
`references/connector-reference.md` and `scripts/erp_client.py` are
ERPNext-specific; swapping backends replaces those two, not this file.

## The general ledger as the single source of truth

Every financial report this skill produces is a projection of the same
underlying data: the general ledger (GL) — a chronological, double-entry
record of every posted transaction, each entry carrying at minimum an
account, a date, a debit or credit amount, and a voucher reference back
to the source document (Journal Entry, Payment Entry, Sales/Purchase
Invoice, etc). Every report below is a different cut, filter, or
aggregation of GL entries — never a separately-maintained figure. If a
report's number can't be traced back to specific GL entries, it isn't a
report this skill should present as authoritative; say so rather than
smoothing over the gap.

**Double-entry invariant:** for any balanced set of GL entries, total
debits equal total credits. This is the single most useful self-check
available, and it's why `scripts/render_report.py` refuses to render a
report with no declared reconciliation check — a trial balance that
doesn't debit-equal-credit isn't a rounding note, it's a sign something
in the query (a filter, a date range, a missing cost-center scope) is
wrong.

## Standard financial statements

**Trial balance** — every account with a non-zero balance for a period,
debits and credits listed separately, totals reconciling to each other.
The starting point for every other statement below; if the trial balance
doesn't tie out, nothing built from it will either.

**Profit & Loss (Income Statement)** — Income accounts minus Expense
accounts for a period, net result = profit/loss for that period. Distinct
from the Balance Sheet in kind, not just presentation: P&L accounts reset
to zero each period (flow, "how much was earned/spent during..."); Balance
Sheet accounts carry a running balance across periods (stock, "what is
owned/owed as of...").

**Balance Sheet** — Assets = Liabilities + Equity, as of a point in time
(not a period). This equality is itself a reconciliation check worth
declaring explicitly in every balance sheet report this skill produces.
A period-end close process rolls the period's P&L result into a retained-
earnings-type Equity account, which is the usual link between the two
statements — if they don't tie together, that close step (in ERPNext:
the **Period Closing Voucher**, which posts the closing GL entries into
its configured account, commonly named "Reserves and Surplus" — see
`references/erpnext-accounting-docs.md`) or an opening-balance
carry-forward is the first place to look, not the report logic itself.

**Cash Flow Statement** — Operating/Investing/Financing sections
reconciling opening cash to closing cash. Same discipline applies — the
reconciling total (opening + net change = closing) is the tie-out check.
Flagged in `SKILL.md`'s Capabilities table as a scope addition beyond the
module plan's original list, since it isn't a variant of another
statement so much as its own construction with its own reconciliation.

## GL drill-down

The mechanism behind every summary figure: given an account, a period, and
optional filters (cost center, party, voucher type), return the
transaction-level GL entries that sum to that figure. The analytical
value is less the drill-down itself than *proving* a headline number by
showing its components — "this ₹2.4L in Travel Expense for March is these
14 entries" — so a user can trust the summary because they can see what's
under it. A drill-down whose entries don't sum to the figure it's meant
to explain is itself a reportable anomaly, not a formatting bug to fix
quietly.

## Cost center / dimension-wise reporting

Most ERPs support tagging GL entries with one or more analytical
dimensions beyond the base chart of accounts — cost center, project,
department, branch, territory (naming varies by system; ERPNext calls
the base one "Cost Center" and supports custom "Accounting Dimensions" on
top, each independently configurable as mandatory for P&L accounts,
Balance Sheet accounts, both, or neither — see
`references/erpnext-accounting-docs.md`). A segment-level report is the
same P&L/trial-balance construction, grouped or filtered by one of these
dimensions instead of (or in addition to) account. **Two failure modes to
guard against, both silent unless checked:** (1) entries with the
dimension field blank/unassigned — an "Unallocated" bucket should be
surfaced explicitly, never dropped, since a segment report that quietly
excludes untagged entries will look internally consistent while being
wrong in total (more likely wherever the org hasn't made that dimension
mandatory on the relevant account types); (2) a segmented total that
doesn't sum back to the company-wide total for the same accounts/period —
always cross-check segment-sum against the unsegmented figure as part of
the reconciliation.

## Variance analysis

Comparing two figures for the same account/segment across either two
periods (period-over-period, e.g. this month vs last month, or
year-over-year) or actual vs budget, and explaining the delta rather than
just stating it. Mechanically: `variance = actual - comparison`,
`variance % = variance / comparison`, computed via
`scripts/render_report.py`'s `compute_variance()` rather than reimplemented
inline — it returns `variance_pct: None` when the comparison base is
zero, which must render as "n/a (base is zero)", never as a fabricated
percentage.
The commentary is the value-add over a bare number: a large variance
isn't automatically a problem (a seasonal revenue spike is expected) nor
is a small one automatically fine (a cost center that should have zero
spend showing any amount is worth flagging regardless of magnitude) — the
persona's judgment is to name *why* a variance likely occurred where the
data supports an explanation (e.g. traceable to a specific large voucher
via drill-down) and flag it as unexplained where it doesn't, rather than
inventing a plausible-sounding cause.

## Custom report / ad hoc query construction

When a user's request doesn't map to one of the standard statements
above, decompose it the same way: identify the accounts/dimensions/period
in scope, decide what GL-level query answers it, and — critically — still
attach at least one reconciliation check before presenting (e.g. "this
custom cut of Q1 marketing spend by cost center sums to the same total as
the unsegmented Q1 Marketing Expense account balance"). An ad hoc report
with no tie-out is the easiest place for a subtle filter bug to produce a
confidently wrong number; the discipline doesn't relax just because the
shape is custom.

## Reconciliation-obsessed, not reconciliation-decorative

The persona's defining trait is that a self-check isn't a courtesy
footnote — it's load-bearing. Concretely, before presenting any figure
derived from a query: (1) name what should equal what (debits/credits,
assets/liabilities+equity, segment-sum/company-total, drill-down-sum/
parent-figure), (2) actually compute both sides from the data returned,
(3) present the result via `scripts/render_report.py`'s
`reconciliation_checks`, which surfaces a mismatch prominently rather
than silently. **A mismatch is not something to paper over or explain
away with a guess** — report it as an anomaly, name the most likely
mechanical cause if the data supports one (unposted draft entries,
`has_more` truncation on the underlying query, a dimension filter
excluding relevant rows), and say plainly when the cause isn't yet known.

**Before declaring a real mismatch, rule out a false one first.** Two
reports pulled with inconsistent scope will disagree without either
number being wrong: mixing a filtered Finance Book pull against an
unfiltered one, or comparing a foreign-currency figure against a
Company-currency figure, produces exactly the shape of a reconciliation
failure without one actually existing. Confirm both sides of any
comparison used the same Finance Book and currency basis before
reporting an anomaly — see `references/erpnext-accounting-docs.md`'s
Finance Book and multi-currency notes for the ERPNext-specific mechanics
behind this.

## Regional/regulatory scope note

Statutory reporting mechanics that are jurisdiction-specific — GST
return formats, TDS computation, e-invoicing — are `qkeee-erp-accounts-
executive`'s domain (operational/compliance lens), not this skill's
(management/analytical lens). This skill's statements (trial balance,
P&L, balance sheet, variance) are general-purpose accounting constructs
that don't carry jurisdiction-specific regulatory content, so no
citation/staleness tracking is needed here the way it is for GST/TDS
material — if a report surfaces a statutory question, point the user to
`qkeee-erp-accounts-executive` rather than answering it from this domain
layer.
