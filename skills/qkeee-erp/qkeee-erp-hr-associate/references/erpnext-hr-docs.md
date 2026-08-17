# Frappe HR (HRMS) documentation map (hr-associate scope)

Curated pointers into `docs.frappe.io/hr` (Frappe HR / HRMS — a separate
app from core ERPNext, confirmed installed on `<erp-instance>`: `hrms
15.61.0`), plus findings confirmed live against `<erp-instance>`.
Runtime reference, not just a build-time note: when unsure how a mechanic behaves, fetch
the linked URL directly (via a harness web-fetch tool, if available)
rather than guessing — this file can drift from the live docs, the URL
is the source of truth.

## Core transactional concepts

| Topic | URL | What's there |
| --- | --- | --- |
| Frappe HR overview | `https://docs.frappe.io/hr` | Module map: Employee lifecycle, Leave & Attendance, Payroll & Taxation, Performance, Expense Claims, Mobile. Thin on per-doctype deep links. |
| Employee | `https://docs.frappe.io/hr/employee` | Mandatory fields, personal/employment/exit sections, User-account linking (auto/manual/import), status values. |
| Employee Onboarding | `https://docs.frappe.io/hr/employee-onboarding` | Job Applicant → Employee bridge, Onboarding Template activities, auto-generated Project/Tasks on submit. |
| Job Opening | `https://docs.frappe.io/hr/job-opening` | Open/Closed status, Staffing Plan-driven vacancy limits, "closed = no new Job Applicant" rule. |
| Job Applicant | `https://docs.frappe.io/hr/job-applicant` | Source tracking (Campaign/Referral/Walk In/Website), Interview dashboard linkage, email-based auto-capture. |
| Interview / Interview Round | `https://docs.frappe.io/hr/interview` | Round-based structure, Interview Feedback restricted to assigned interviewers, calendar view, reschedule flow. |
| Job Offer | `https://docs.frappe.io/hr/job-offer` | Awaiting Response/Accepted/Rejected states, one-to-one Job Applicant link, Save-and-Submit. Note: this URL, not `/hr/offer-letter` (that path 404s — the doctype is "Job Offer" in current Frappe HR, "Offer Letter" is the module-plan/legacy name for the same concept). |
| Leave Application | `https://docs.frappe.io/hr/leave-application` | Approval flow (Open → Approved/Rejected → submit), Leave Allocation prerequisite, single-allocation-period constraint. |
| Attendance | `https://docs.frappe.io/hr/attendance` | Present/Absent/On Leave/Half Day (+"Work From Home" confirmed live), manual/bulk/auto marking, Marking Unmarked Attendance for regularization. |
| Employee Separation | `https://docs.frappe.io/hr/employee-separation` | Separation Template activities, auto-generated Project/Tasks on submit, same checklist mechanism as Onboarding. |

## Employee — field grounding (live)

Mandatory per `GET /api/resource/DocType/Employee`: `first_name`,
`gender` (Link → Gender; confirmed values on this instance: Male,
Female, Other, Transgender, Genderqueer), `date_of_birth`,
`date_of_joining`, `status` (Select: Active/Inactive/Suspended/Left),
`company`. Relevant non-mandatory fields confirmed present:
`user_id` (Link → User, the account-linking field), `reports_to`
(Link → Employee), `department`, `designation`, `holiday_list` (Link →
Holiday List), `relieving_date`, `employee_number`,
`create_user_permission` (Check).

**Practical requirement not visible in `reqd` flags — same pattern
`qkeee-erp-procurement`'s build found on Purchase Order's warehouse
field:** setting `status: "Left"` without a `relieving_date` is
accepted by the schema (neither field forces the other at the `reqd`
level) but is an incomplete exit in practice — `scripts/
render_employee_draft.py` checks this explicitly.

**Live create confirmed**: created `HR-EMP-00002`
(`first_name: "qkeee-erp-hr-associate"`, `gender: "Male"`,
`date_of_birth: "1995-01-01"`, `date_of_joining: "2026-08-10"`,
`company: "Qkeee LLP"`, `status: "Active"`) — succeeded immediately,
`docstatus: 0` (Employee is **not** a submittable doctype — confirmed
via `GET /api/resource/DocType/Employee` → `is_submittable: 0` — no
draft/submit distinction the way Purchase Order or Leave Application
have one).

## Leave Application — field grounding + live round trip

Mandatory: `naming_series`, `employee`, `leave_type`, `company`,
`from_date`, `to_date`, `posting_date`, `status` (Select:
Open/Approved/Rejected/Cancelled). `is_submittable: 1` — confirmed.

**Two live-discovered gates on submit, neither visible from the
schema's `reqd` flags alone:**

1. **`ValidationError: Only Leave Applications with status 'Approved'
   and 'Rejected' can be submitted`** — a freshly-created application
   defaults to `status: "Open"`; submit fails until the status is
   explicitly updated to `Approved` (or `Rejected`) first. This is the
   API-level shape of the "approver reviews, then approves" step —
   there's no separate "approve" RPC method; approving *is* setting
   `status` via a normal `update` call, then submitting.
2. **`ValidationError: Please set a default Holiday List for Employee
   <X> or Company <Y>`** — submit additionally requires a resolvable
   Holiday List (on the Employee record or the Company) even for a
   Leave Without Pay application that doesn't touch a leave balance.
   `<erp-instance>` has only one Holiday List configured (`"US Holiday
   List"`) — an org's real instance should have region-appropriate
   Holiday Lists per company; don't assume one exists without checking
   (`query "Holiday List"`).

**Full round trip confirmed**: created `HR-LAP-2026-00001` (employee
`HR-EMP-00002`, leave_type `"Leave Without Pay"`, company `Qkeee LLP`,
1 day) with `status: "Open"` — submit failed with gate #1 above;
updated `status: "Approved"` via `update` — submit failed with gate #2;
set `holiday_list: "US Holiday List"` on the Employee via `update` —
submit succeeded, `docstatus: 1`, `status: "Approved"`. Cancelled via
`mutate cancel` — `docstatus: 2`, `status: "Cancelled"`.

**New finding, not previously documented anywhere in the library:
approving and submitting a Leave Application auto-creates an Attendance
record** (`status: "On Leave"`, `leave_application` field linking back)
for the covered date(s) — confirmed live: `HR-ATT-2026-00001` appeared
automatically after the Leave Application submit above, with no
separate Attendance-creation call made. When the Leave Application was
subsequently cancelled, the linked Attendance record was **automatically
cancelled too** (`docstatus: 2` observed on inspection, no explicit
cancel call issued against it) — this cascading behavior should be
explained to the user as expected system behavior when discussing leave
approval, not treated as a surprise side effect. See
`references/domain-knowledge.md`.

## Job Applicant — field grounding + autoname finding (live)

Mandatory: `applicant_name`, `email_id` (Email fieldtype), `status`
(Select: Open/Replied/Rejected/Hold/Accepted). `is_submittable: 0` —
confirmed, same "no draft/submit distinction" shape as Employee and
Supplier (`qkeee-erp-procurement`'s build found the same pattern for
Supplier).

**Autonaming finding: a Job Applicant's `name` is its `email_id`, not a
generated series** — confirmed live: creating one with
`email_id: "test.applicant@example.com"` produced a record named
literally `test.applicant@example.com`, not an `HR-JA-...`-style name.
This matters for lookups (query/reference this doctype by email, not by
an assumed naming series) and for re-application handling. **Confirmed
live via adversarial review: a second Job Applicant `create`
with the same `email_id` neither updates the existing record nor
conflicts** — it succeeds with HTTP 200 and gets auto-suffixed to
`<email>-1` (standard Frappe duplicate-name handling), silently
producing two disconnected records for the same candidate. Always
`query "Job Applicant" --filters '[["email_id","=","<email>"]]'` before
creating, and update the existing record if one is found — never rely
on a create call to fail or merge on its own for a re-application.

## Job Offer — field grounding (live schema, not live round-tripped)

Mandatory: `job_applicant` (Link → Job Applicant), `applicant_name`,
`offer_date`, `designation`, `company`. `is_submittable: 1` — confirmed.
Real status values per docs: Awaiting Response / Accepted / Rejected.
**Not live create/submit tested in this build** — this is one of this
skill's two hard-advisory capabilities (never auto-committed regardless
of mode), so the round-trip that matters is the human actually
extending the offer in ERPNext themselves; this skill's job is staging
the draft via `scripts/render_advisory_draft.py`, not confirming the
write path end-to-end the way read-write capabilities are.

## Employee Onboarding / Employee Separation — field grounding (live schema, not live round-tripped)

**Employee Onboarding** mandatory: `job_applicant`, `job_offer`,
`company`, `employee_name`, `date_of_joining`, `boarding_begins_on`.
**Employee Separation** mandatory: `employee`, `company`,
`boarding_begins_on`. Both `is_submittable: 1` — confirmed. Per docs, submitting either
auto-generates a Project with Tasks per template activity, and status
flips to "Completed" once every activity is done — not independently
live-tested in this build (Employee Onboarding is this skill's second
hard-advisory capability, so its round trip is inherently out of scope;
Employee Separation is a read-write-capable capability but wasn't
round-tripped here). **Before promising the checklist-generation
behavior for an Employee Separation on any target org's instance, run
`python scripts/erp_client.py --tag <tag> query "Employee Separation
Template"` first and confirm at least one exists** — don't promise
auto-generated Tasks/Project as if the mechanism were self-verified;
it's a schema-level fact only, not something this build's live
round-trip actually exercised end to end the way Leave Application or
Job Applicant were.

## Interview / Interview Round — field grounding (live schema only)

**Interview** mandatory: `interview_round`, `job_applicant`, `status`
(Select: Pending/Under Review/Cleared/Rejected), `scheduled_on`,
`from_time`, `to_time`. **Interview Round** mandatory: `round_name`,
`expected_skill_set` (child table). **Interview Feedback** mandatory:
`interview`, `interview_round`, `interviewer` (Link → User), `result`
(Select: Cleared/Rejected), `skill_assessment` (child table).
Confirmed live: `is_submittable: 1` on **Interview**,
`is_submittable: 1` on **Interview Feedback**, `is_submittable: 0` on
**Interview Round** (the round definition itself isn't a workflow
document — only the scheduled Interview and the recorded Feedback go
through draft/submit).

## HR reports — no live query validation (gap)

Unlike every other capability in this file, no headcount/birthday/
probation-ending/attendance-summary query was live-round-tripped against
`<erp-instance>` during this build — `render_report.py` is unit-tested
against synthetic input only. In particular, ERPNext has no direct
month/day filter on a Date field (`date_of_birth`), so a birthday report
needs either a report-specific query approach or client-side filtering
after a broader fetch — neither was confirmed to work live. Treat the
first real use of the HR-reports capability against a target org as
this capability's effective validation, same caution as Employee
Separation above, and confirm the query approach works before
presenting reconciliation numbers as trustworthy.

## Payroll — Salary Slip (field grounding)

**Mandatory fields** (confirmed via create + live traceback):
- `employee` (Link → Employee)
- `company` (Link → Company)
- `posting_date` (Date)
- `start_date` and `end_date` (Date — payroll period)
- `salary_structure` (Link → Salary Structure)

**Practical prerequisite — not a field but enforced by validation:**
A **Salary Structure Assignment** must exist for the employee, with
`from_date` ≤ the slip's `start_date`, before the slip can be created.
The assignment itself is a separate doctype (`Salary Structure
Assignment`), submittable (`docstatus` workflow), with mandatory
fields: `employee`, `salary_structure`, `from_date`, `company`.

**Autoname format:** `Sal Slip/None/<NNNNN>` — format confirmed live.
Search for salary slips by `employee` + `start_date` combination, not
by assumed naming pattern.

**Response payload** (live-confirmed): a successfully created draft
includes calculated fields — `gross_pay`, `total_deduction`, `net_pay`,
`total_working_days`, `payment_days`, `leave_without_pay`. Child tables
`earnings` and `deductions` auto-populate from the salary structure.
`is_submittable: 1` — confirmed.

**Submit workflow** (via REST API):
1. `GET /api/resource/Salary Slip/<name>` — fetch full doc
2. Strip server-managed fields: `owner`, `creation`, `modified`,
   `modified_by`, `docstatus`, `idx`, `_user_tags`, `_comments`,
   `_assign`, `_liked_by`, `__onload`, `__last_sync_on`, `amended_from`,
   `permissions`
3. `POST /api/method/frappe.client.submit` with `{"doc": <cleaned doc>}`
4. Response `docstatus: 1` confirms submission
5. Submit does NOT auto-create Journal Entry or Payment Entry — those
   are separate payroll-run steps (see Payroll Entry doctype)

**Key pitfall: response key difference.** `create` returns under
`"data"`, `submit` returns under `"message"`. The most common error
within this skill's near-neighbour payroll work is copying a
response-check snippet between the two and reading the wrong key.

**Limitations** (see `payroll-batch-operations.md` for the full batch
pattern):
- Salary Slips are created individually by employee+period, not en
  masse — there is no "Create Salary Slip for all employees" bulk REST
  endpoint; batch scripting is required for more than one employee.
- Deduplication: creating two slips for the same employee+period does
  NOT fail — Frappe creates a second draft with an autoname suffix
  (`Sal Slip/None/00001`, `Sal Slip/None/00002`).

## Staleness note

Fetched/verified against a live instance. Doctype field lists and
mandatory flags should be reconfirmed against the target org's
instance directly (`GET /api/resource/DocType/<DocType Name>`) rather
than assumed from this file — this instance's specific configuration
(e.g. which Holiday Lists, Leave Types, or Onboarding/Separation
Templates exist) will differ per org.
