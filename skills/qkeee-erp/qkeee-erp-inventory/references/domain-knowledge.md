# qkeee-erp-inventory domain knowledge

ERP-agnostic warehouse/inventory-controller knowledge. ERPNext specifics
are called out as pointers to `references/erpnext-stock-docs.md` and
`references/connector-reference.md`, not baked into the concepts here —
this file should read the same against any ERP backend.

## The persona's stance

A warehouse/inventory controller treats stock figures as representing
**real physical goods**, not just numbers in a system. A stock record
that says "20 units in Stores" is a claim about the physical world; if
it's wrong, someone downstream (production, a picker, a buyer deciding
whether to reorder) makes a decision on false information. This is why
every adjustment — a transfer, a reconciliation — states its impact
plainly before it happens: the controller is the last check between "a
number changed" and "the number now lies."

## Core concepts

**Stock level** is always item + warehouse, never just item. The same
item can have different quantities in different warehouses, and a
"total stock" figure is only meaningful as an explicit sum across a
named set of warehouses — never assume "stock" means "everywhere."

**A stock movement (transfer/issue/receipt)** records goods physically
moving: into a warehouse (receipt, from a purchase or production), out
of a warehouse (issue, to consumption or scrap), or between two
warehouses (transfer). The core integrity question for a movement is
always the same regardless of ERP: *can the source location actually
supply what's being moved out of it, right now?* A system that lets you
draft a movement without checking this is deferring the check to a
later, more consequential moment (a failed post, or worse, a silently
wrong balance) — the controller's job is to check it up front.

**Stock reconciliation** corrects the system's record of what's on hand
to match a physical count. This is fundamentally different from a
transfer: a transfer records something that happened (goods moved); a
reconciliation asserts a fact (this is what's really there right now).
The core integrity question is: *does the reconciliation SET the
balance to the counted figure, or does it risk ADDING to whatever the
system already thinks is there?* Getting this wrong is the single most
dangerous failure mode in this domain — it doesn't just misrecord one
transaction, it corrupts the baseline every future transaction builds
on. Always state the delta (counted vs. system-recorded, both qty and
value) plainly before committing, so a human can sanity-check the size
of the jump before it becomes fact.

**Batch and serial tracking** exist because not every unit of an item
is interchangeable — a batch groups units that share an origin (a
production run, a lot from a supplier) and typically an expiry or
quality attribute; a serial number identifies one physical unit
individually (for warranty, traceability, or high-value goods). Where
an item is batch/serial-tracked, "the stock level" is really the sum
across batches/serials, and any *correction* to that stock level must
be attributed to the right batch/serial, not just adjusted at the item
level — an ERP that reconciles per-batch (rather than per-item) means a
correction that doesn't name the right batch can silently create a new
one instead of fixing the existing record. This is a domain-level
principle, not an ERPNext quirk: any system with batch/serial tracking
faces the same "which bucket does this correction belong to" problem.

**Reorder triggers.** When stock for an item falls below a threshold
that makes sense for that item (lead time, consumption rate, safety
stock), a request to replenish it should be raised — but raising the
request is not the same as committing to buy or produce anything. A
reorder trigger produces a *request* (typically routed to Procurement
for a purchased item, or Manufacturing for a produced one); the
purchasing/production decision itself belongs to whoever owns that
process, not to inventory control.

**Batch/serial trace.** Being able to answer "where has this
batch/serial been, and what happened to it" is a core audit capability
— every movement of a tracked unit should be reconstructable as a
chronological sequence of events (received, transferred, issued,
adjusted), each with a resulting balance. A trace that can't verify its
own running balance is internally consistent (each event's resulting
balance should equal the prior balance plus that event's movement) is a
trace that might be silently missing events — verify the arithmetic,
don't just list the rows.

## Risk posture by capability

- **Stock level query, batch/serial trace** — read-only, low risk.
  Value is in getting the numbers right and reconciling them (sum of
  parts equals the stated total), not in any write action.
- **Stock transfer** — moderate risk. Physically real (goods move,
  value moves with them) but reversible in principle (a return transfer
  undoes it). The controller's job is verifying the source can actually
  supply what's claimed, before the movement is drawn up as ready —
  waiting until submission to find out is a false economy.
- **Stock reconciliation** — highest risk of this skill's capabilities.
  A wrong reconciliation doesn't just misrecord one event, it becomes
  the new baseline everything after it is measured against. State the
  qty and value delta plainly, and never treat a reconciliation as
  "just another write" — it's closer in spirit to restating an opening
  balance than to recording a transaction.
- **Reorder trigger (Material Request)** — low risk. It's a request,
  not a commitment; the downstream buy/make decision belongs to another
  role.

## Extension point

To target a different ERP backend, replace
`references/connector-reference.md`, `references/erpnext-stock-docs.md`,
and `scripts/erp_client.py`. Everything above should stay accurate
regardless of backend — the business logic of "does the source have
enough," "does the reconciliation set or add," and "does the trace's
running balance hold together" is ERP-agnostic in substance.
