# qkeee-erp-sales domain knowledge

ERP-agnostic sales/order-management expertise. ERPNext specifics are
called out as pointers into `references/erpnext-selling-docs.md` and
`references/connector-reference.md`, not baked into the concepts below —
this file should read the same regardless of which ERP backend a future
connector layer targets.

## The persona

A customer-facing sales executive: responsive to customer requests, but
careful about the line between "an indicative quote" and "a commitment
the business is now on the hook for." The single habit that separates a
good sales executive from a risky one is not treating a Quotation as a
Sales Order — the two are different in kind, not just in ERPNext status
field. A Quotation is an offer; nothing is owed until the customer
accepts and it becomes an order.

## Customer onboarding

A customer record exists so the business can legally invoice and
recognize revenue against a named counterparty, and so sales history
(quotations, orders, credit exposure) accumulates in one place instead
of being reconstructed from memory each time. The functional minimum for
a *usable* customer record — independent of which ERP stores it — is:

- **Identity**: legal/trading name, and whether it's a company,
  individual, partnership, etc. (affects tax treatment and contract
  capacity).
- **Classification**: a customer group/segment and a territory —
  without these, sales reporting can't be sliced by segment or region,
  which is usually the first thing a sales manager asks for.
  ERP-agnostic reason: reporting hierarchies are built on these fields;
  skipping them at onboarding means retrofitting every historical
  transaction later.
- **A reachable primary contact**: at least one working channel (email
  or phone) for the person who actually receives quotations and
  purchase confirmations — a customer record with a company name and
  nothing else is not actionable; nobody can be quoted, invoiced-with-
  a-cc, or chased for a PO number.
- **Tax identifier**, where the counterparty's jurisdiction requires one
  for B2B invoicing — captured at onboarding time rather than chased
  down later when the first invoice is due.

**Why a stricter bar than "the ERP will let you save it":** most ERPs'
own mandatory-field validation is deliberately minimal (so ad hoc/manual
entry isn't blocked), which means a system-enforced minimum is *not* the
same as a functionally complete customer record. A customer onboarded
with only a name is a data-quality debt the business pays later, at the
worst possible time (mid-negotiation, mid-invoice-dispute). This
skill's onboarding capability enforces the fuller, functional bar in
code (see `scripts/render_customer_draft.py`), not just at the
API/schema level.

## Quotation lifecycle

A Quotation moves through, functionally, the same shape in any sales
process: **draft → sent → (accepted | declined | expired)**. The
specific status vocabulary an ERP uses varies (Draft/Open/Replied/
Ordered/Lost is one such vocabulary; "sent"/"won"/"lost" is another),
but the underlying stages are universal — this is what makes a
pipeline-lite report meaningful across backends.

**The commitment boundary is the non-negotiable to protect.** Creating
a Quotation record is cheap and reversible — it's a draft document, not
a promise. *Formalizing* it (whatever the ERP calls the transition —
"submit," "send," "issue") is the point past which the business is
implicitly on record having quoted a price and terms to a named
customer. A sales assistant that auto-formalizes a quotation the moment
it's drafted removes the human's last chance to sanity-check pricing,
terms, and whether the customer relationship even warrants that offer
yet. This is why the capability is scoped to "draft, never
auto-submit" — the formalization step is always a separate, explicitly
confirmed action.

**Line-item integrity matters more than header completeness.** A
quotation with a vague header but exact, correctly-priced, correctly-
identified line items is more useful (and less embarrassing to correct)
than one with a polished header and item lines built from a fuzzy match
on item name. Resolving each line to a real, unambiguous product/service
record (not just a name string) before drafting is worth the extra
step — a name-only line risks silently quoting the wrong item, at the
wrong price, for something the business may not even be able to sell
(some items are stock/manufacturing inputs, never meant to be sold
directly to a customer — the sales-eligibility check exists precisely
to catch this class of mistake before it reaches a customer's inbox).

## Sales Order & fulfilment tracking

Once a customer accepts a quotation (or simply places an order
directly), the Sales Order becomes the operational record of record:
what was promised, by when, and — as fulfilment happens — how much of
that promise has actually been delivered and billed. The two axes that
matter for "where does this order stand" are independent of each other
and must be tracked separately:

- **Delivery fulfilment** — how much of what was ordered has physically
  shipped/been handed over.
- **Billing fulfilment** — how much of what was ordered has been
  invoiced.

An order can be fully delivered and not yet billed, fully billed in
advance and not yet delivered, or partially both — treating "status" as
a single flat field loses information a sales executive or their
manager actually needs (e.g. "why hasn't this been billed if it shipped
three weeks ago?"). A good status report surfaces both axes explicitly,
not a single collapsed status string.

**Traceability from delivery back to the originating order line
matters.** A shipment that isn't cleanly linked back to the specific
order line it fulfils breaks the fulfilment-percentage math and makes
"is this order done yet" an open question instead of a computed fact.
This is a common failure mode worth checking for explicitly when a
delivery/order status looks inconsistent, rather than assuming the
numbers are simply wrong.

## Sales pipeline-lite reporting

"Pipeline" in the lite sense this skill supports means: how many
quotations are sitting in each stage, and how much open order value is
outstanding and how overdue it is — not a full CRM funnel with
probability-weighted forecasting. Two things make this trustworthy
rather than just a number:

1. **The stage counts must reconcile with the total queried.** If a
   pipeline report's stage buckets don't sum to the number of records
   actually queried, something (an unrecognized status value, a filter
   bug, silent pagination truncation) is hiding data, and the report
   should say so rather than presenting a plausible-looking but wrong
   picture.
2. **Overdue/pending order value is the operationally actionable
   number**, more than a raw pipeline count — a stalled quotation is a
   sales problem, an overdue *order* is a customer-relationship and
   possibly a cash-flow problem, and the two deserve to be reported
   distinctly.

## Deliberate scope boundary

This skill covers the ERP's Selling/order-management surface — it is
not a CRM. Lead management, opportunity scoring, campaign tracking, and
probability-weighted forecasting are out of scope; a customer already
exists (or is onboarded here) and the objects handled are Quotation →
Sales Order → Delivery Note. If deeper CRM capability is ever needed,
that's a separate skill's problem to solve, not a reason to widen this
one's surface.
