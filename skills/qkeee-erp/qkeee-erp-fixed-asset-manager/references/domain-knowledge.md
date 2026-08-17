# qkeee-erp-fixed-asset-manager domain knowledge

ERP-agnostic in substance — this is what a meticulous, audit-minded
fixed-asset manager knows about the asset lifecycle, independent of
which system executes it. `references/connector-reference.md` and
`scripts/erp_client.py` are the ERPNext-specific layer; ERPNext-specific
asides below point at `references/erpnext-assets-docs.md` rather than
being baked into the concepts themselves.

## Why asset value and location integrity are sacrosanct

A fixed asset record is the single source of truth for three things an
organization is accountable for: **what it owns, where it is, and what
it's worth on the books.** Each of those degrades independently if
handled carelessly:

- **Value integrity** breaks when a capitalization is recorded with the
  wrong cost basis, when a depreciation run posts against the wrong
  base, or when a disposal is recorded without correctly closing out
  the remaining book value — any of these misstates the balance sheet,
  not just one asset's record.
- **Location integrity** breaks when a transfer is recorded against a
  stale or fabricated "current location" — the receiving location looks
  correct, but the movement history (and anyone trying to physically
  locate the asset later) is now wrong. This is why a movement's stated
  source must be verified against the asset's actual last-known
  location before it's treated as ready, not just trusted at face value.

## Capitalization — establishing an asset's existence and cost basis

An asset enters the books one of two ways: **capitalized from an actual
purchase** (a real Purchase Receipt/Invoice exists behind it — the
system can trace exactly what was bought, from whom, for how much), or
**recorded as an existing/opening-balance asset** (no purchase document
in this system — typically a migration, an asset acquired before this
system was in use, or a gift/found asset). These are fundamentally
different provenance claims and must never be left ambiguous — a
capitalization staged as "ready" without stating which one it is is
itself a gap, not a detail to fill in later.

- **Cost basis** (gross purchase amount) is the anchor for everything
  downstream — depreciation, disposal gain/loss, insurance valuation.
  A zero or missing cost basis isn't "fine for now"; it's a decision
  ("this asset is genuinely free — a donation") that must be stated
  explicitly, never silently defaulted.
- **Category matters for more than reporting.** An asset's category
  typically maps to the specific fixed-asset / accumulated-depreciation
  / depreciation-expense accounts a depreciation run posts to — an
  asset capitalized without a category resolved has nowhere for a
  future depreciation entry to land, a problem that surfaces much later
  (at the first depreciation run) if not caught at capitalization time.
- **Depreciation configuration, if the asset is depreciable, must be
  complete at capitalization** — method, useful life (number of
  periods), frequency, and start date. An incomplete depreciation setup
  doesn't fail loudly; it just produces no schedule, which looks like
  "nothing due yet" rather than "not configured", so it's worth
  verifying explicitly rather than assuming silence means "on track."

## Depreciation — a recurring, irreversible-in-spirit event

Depreciation is not a single event; it's a *schedule* — a sequence of
periods, each with its own amount, that gets posted incrementally over
the asset's useful life. Two properties of a depreciation run make it
worth double-confirming rather than treating like a routine read/write:

- **A single "run depreciation" action can cover more than one period
  at once.** If a schedule has fallen behind (the org hasn't run
  depreciation in a few months), the next run catches up everything
  overdue in one shot — a user who thinks they're approving "this
  month's depreciation" may actually be approving six months' worth.
  State the period count and total amount explicitly before either
  confirmation, never just "run depreciation for `<asset>`."
- **Depreciation entries, once posted, are ledger-touching Journal
  Entries** — reversible only via cancellation (and cancellation
  reopens the accounting question of "was this ever really posted"),
  never a casual undo. Treat a depreciation run with the same
  seriousness as any other irreversible-in-spirit financial posting.
- **Resulting book value must be computed from the schedule itself**,
  not assumed from wherever the asset's summary field last said — a
  system's own top-level "current value" field can lag behind what the
  schedule has actually posted (see `references/erpnext-assets-docs.md`
  for the concrete ERPNext finding). Always state the *computed*
  resulting value from the schedule rows being posted, not a cached
  summary figure.

## Asset transfer / movement

A transfer, receipt, or issue changes an asset's custodian and/or
physical location — this is the mechanism that keeps the system's
record of "where is this asset" honest against reality. The critical
discipline: **a transfer's stated source location must be verified
against the asset's actual current location before it's treated as
ready**, not assumed correct because the user said so. A system that
accepts a transfer with a fabricated source silently corrupts its own
audit trail — the destination will be right, but the history of how the
asset got there will not be, and that history is exactly what a later
audit or "where did this asset go missing" investigation depends on.

## Asset maintenance

Preventive maintenance scheduling exists to catch failures before they
happen, not just to log them after. A maintenance schedule is only
useful if its due dates are actually watched — reporting "asset X is
overdue for maintenance" is as much this persona's job as scheduling
the maintenance in the first place. Maintenance and repair are related
but distinct: a *schedule* is planned, recurring, preventive work; a
*repair* is unplanned, reactive, and — when the repair cost is material
enough — may itself be capitalized (added to the asset's book value)
rather than expensed, a judgment call the org's own accounting policy
should govern, not a default this skill assumes on the user's behalf.

## Disposal — scrap vs. sale, and what "irreversible in practice" means

An asset's life ends one of two ways: **scrapped** (retired with no
consideration received — the remaining book value is a pure write-off)
or **sold** (consideration received, and the difference between
proceeds and remaining book value realizes a gain or loss). These are
not interchangeable and must be distinguished explicitly:

- **Scrap** has no gain/loss calculation — the entire remaining book
  value is the write-off amount, full stop.
- **Sale** requires the remaining book value AND the sale proceeds to
  state a gain or loss — presenting one without the other leaves the
  financial impact unstated, which is exactly what disposal
  confirmation exists to prevent.
- **A stated reason is not optional.** "End of useful life", "damaged
  beyond repair", "replaced by `<new asset>`" — the reason is part of
  the audit trail a later reviewer needs; "dispose it" with no reason
  recorded is a gap in the record, not a minor omission.
- **Disposal is technically cancelable in most systems but never a
  casual undo** — the accounting effect (a write-off or a realized
  gain/loss) has already happened by the time cancellation is even
  possible, and un-doing it reopens exactly the kind of accounting
  question a clean audit trail is meant to avoid. Treat disposal with
  the same double-confirm discipline as a depreciation run, for the
  same underlying reason: technically reversible is not the same as
  practically costless to reverse.

## Asset audit / physical verification

The point of a physical verification is closing the gap between "what
the system says exists, where it says it is" and "what a person can
actually go find" — a system-only view can drift from reality for a
long time before anyone notices (an asset quietly disposed of by a
department without going through the system, an asset moved without a
recorded transfer). A useful audit checklist states, per asset in
scope: expected location, whether it was actually located, and — if
not — what the discrepancy is (missing, found elsewhere, already
disposed but not recorded). A checklist that only lists "verified: yes"
without capturing the *unverified* cases with their specific gap isn't
doing the job an audit is for.

## Regional/regulatory scope note

Depreciation *method* mechanics (straight-line, declining-balance,
useful-life conventions) are this skill's concern; the tax-reporting
consequences of depreciation (accelerated depreciation for tax purposes
vs. book depreciation, region-specific capital allowance rules) are a
downstream compliance concern this skill does not attempt to adjudicate
— state the book-basis figures accurately and flag when a user's
question is really about tax treatment, which belongs with whoever owns
that org's tax compliance function (conceptually adjacent to
`qkeee-erp-accounts-executive`'s GST/TDS scope, though fixed-asset tax
treatment specifically isn't in either skill's built capability table).
