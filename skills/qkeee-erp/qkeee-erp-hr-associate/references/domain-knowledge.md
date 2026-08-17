# qkeee-erp-hr-associate domain knowledge

ERP-agnostic in substance — this is what a warm-but-process-compliant,
privacy-conscious HR generalist/associate knows about transactional HR
and talent acquisition, independent of which system executes it.
`references/connector-reference.md` and `scripts/erp_client.py` are the
ERPNext-specific layer; ERPNext-specific asides below point at
`references/erpnext-hr-docs.md` rather than being baked into the
concepts themselves.

## Employee PII — scope discipline, not a blanket lock

This skill's non-negotiable isn't "never touch PII" — HR work is
inherently PII-heavy (compensation, ID documents, personal contact
details, health/family information all legitimately live on an
Employee record). The discipline is **scope**: surface or write a
sensitive field only when the current task actually needs it, not as a
side effect of a broader query. Pulling an employee's bank account
number while answering "what department is Jane in" is a scope
violation even though both facts live on the same record.
`scripts/render_employee_draft.py` flags every PII-sensitive field
present in a draft explicitly, rather than rendering it identically to
an ordinary field like department — the point isn't to block it, it's
to make the presence of sensitive data visible enough that a reviewer
notices if it's there for no reason.

## New employee onboarding — a checklist, not a single write

Onboarding isn't "create one Employee record" — it's a sequence
(Employee record → linked User account, if warranted → onboarding
checklist/activities) where each step has its own failure mode. A
User account tied to an Employee grants system access; creating one
prematurely (before a start date, before the role is confirmed) is a
different mistake than forgetting to create one at all. Treat the
checklist as the actual deliverable, not the Employee record alone —
"onboarding is done" means every activity is complete, not that a
record exists in the system.

## Update employee details — confirm before touching, always

An update to an existing Employee record is lower-drama than a new
hire but not lower-stakes — a wrong department, a wrong reporting
line, or a wrong status change (see below) has real downstream effects
(wrong approver chain, wrong leave policy, payroll implications).
Confirm the specific field(s) changing and the new value before any
write, every time — never infer "update the employee" as license to
touch fields the user didn't actually ask about.

## Leave application / balance check

A leave balance is a derived fact (allocated minus already-applied,
for the relevant leave type and period), not something to state from
memory — always check the current Leave Allocation for that employee/
leave-type/period rather than assuming a policy default applies
uniformly. **Status matters for submission, not just balance**: a Leave
Application only becomes an actual commitment once its status reflects
approval and it's submitted — a drafted-but-unapproved application
isn't yet time off on the books, and a submission attempt on an
unapproved application will fail (see `references/erpnext-hr-docs.md`
for the exact mechanic). **Approving a leave application is itself a
downstream-triggering action, not just a status flip** — on ERPNext,
approving and submitting a leave application automatically marks
Attendance for the covered dates; treat that as expected system
behavior to explain to the user, not a surprising side effect to hide.

## Attendance query / regularization

Attendance is either marked directly (present/absent/half-day/on-leave/
work-from-home) or derived automatically from an approved Leave
Application covering that date — **a discrepancy between "what
attendance shows" and "what actually happened" needs regularization
through the correct originating document**, not a direct attendance
edit that leaves the Leave Application record inconsistent with it. If
an attendance record traces back to a Leave Application, correcting it
means correcting the leave record, not overwriting the derived
attendance entry in isolation.

## Employee separation / exit checklist

Like onboarding, this is a checklist-driven process (collect assets,
clear dues, revoke system access, exit interview), not a single
"mark employee as left" action — and the checklist activities are
exactly the kind of process where skipping one (e.g. forgetting to
revoke system access) has a real security/compliance consequence, not
just an administrative loose end. **Setting an employee's status to
"Left" has its own mandatory follow-through**: a relieving date must
be captured alongside the status change (see `references/erpnext-hr-
docs.md` for the exact field) — a status change without it is an
incomplete exit, not a completed one, even if the record technically
saves.

## Job Opening → Job Applicant → Interview → Offer — recruitment funnel discipline

Each stage exists to prevent a common hiring mistake:
- **Job Opening** caps how many applicants can reasonably be pursued
  for a role — a closed opening shouldn't quietly accept new
  applicants (this is enforced by ERPNext itself: a closed Job Opening
  can't take new Job Applicants against it, see `references/erpnext-hr-
  docs.md`).
- **Job Applicant** intake should capture the source (referral,
  campaign, walk-in, website) — not just for reporting, but because
  source materially affects how a candidate should be engaged (a
  referral deserves a different first touch than a cold application).
- **Interview** feedback should come only from interviewers actually
  assigned to that interview round — a well-meaning comment from
  someone outside the round isn't a substitute for structured feedback
  from the people who actually evaluated the candidate, and ERPNext
  enforces this at the API level (only assigned interviewers can submit
  Interview Feedback).
- **Offer** (Job Offer / "Offer Letter") is the funnel's most
  consequential step — compensation terms, start date, and role
  details all become a commitment once extended. This is why it's one
  of this skill's two hard-advisory capabilities (see the non-negotiable
  in `SKILL.md`): never auto-committed, regardless of read-write mode.

## Regional/regulatory scope note

Compensation-tax mechanics that consume an Employee's data downstream
(TDS withholding on salary, PF/ESI where applicable) are `qkeee-erp-
accounts-executive`'s domain via ERPNext's payroll/tax modules, not
duplicated here — this skill's concern stops at capturing accurate
employee data and the checklist processes above, not at the tax
mechanics that later reference it.
