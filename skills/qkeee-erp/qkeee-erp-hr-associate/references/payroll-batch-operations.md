# Payroll operations — batch Salary Slip creation & submission

Covers creating and submitting Salary Slips for one or many employees
across pay periods via `scripts/erp_client.py`, when no dedicated bulk
payroll-run endpoint is available (see Known Limitations below for
when Payroll Entry is the better path instead).

## Prerequisite: Salary Structure Assignment

Before creating any Salary Slip, the employee **must** have a
**submitted** Salary Structure Assignment covering the pay period:

- DocType: `Salary Structure Assignment`, `is_submittable: 1` —
  assignments must be submitted (`docstatus: 1`), not just saved as
  draft.
- Key field: `from_date` ≤ the slip's `start_date` — checked at
  slip-creation time, not submit time. Failure surfaces as a
  `ValidationError`: *"Please assign a Salary Structure for Employee
  <name> applicable from or before <date> first"*.

Check this before promising a batch run will work:

```
python scripts/erp_client.py --tag <tag> query "Salary Structure Assignment" \
  --filters '[["employee","=","<employee>"],["docstatus","=",1]]' \
  --fields '["name","salary_structure","from_date"]'
```

## Creating and submitting a single slip

```
python scripts/erp_client.py --tag <tag> --mode read-write --requested-by <requester> \
  mutate "Salary Slip" create --payload '{"employee":"<employee>","company":"<company>",
  "posting_date":"<date>","start_date":"<period-start>","end_date":"<period-end>",
  "salary_structure":"<structure>"}'
```

`create` returns the new record under `"data"`; the name follows the
pattern `Sal Slip/None/<NNNNN>`.

```
python scripts/erp_client.py --tag <tag> --mode read-write --requested-by <requester> \
  mutate "Salary Slip" submit --name "<name>"
```

`mutate_resource()`'s `submit` action already does the required
fetch-full-doc → `frappe.client.submit` sequence internally — don't
hand-roll that GET/POST cycle when going through `erp_client.py`. It
reposts the fetched doc verbatim (submit is not a diff), which is
expected — see `connector-reference.md`'s submit note. **Key pitfall:
response key difference** —
`create` returns the record under `"data"`, `submit` returns it under
`"message"`. The most common error in payroll scripting is copying a
response-check snippet between the two and reading the wrong key.

## Batch runs (many employees × many periods)

```
Outer loop: PERIODS
  Inner loop: EMPLOYEES
    Step A: search by employee+start_date first — skip if an
            already-submitted slip exists (dedup, see below)
    Step B: create → save returned name
    Step C: submit by name
    pace calls — don't fire the API back-to-back in a tight loop
    progress report every ~10 calls on a run long enough to look hung
```

**Rate-limiting discipline:** pace consecutive API calls rather than
firing them back-to-back — rapid-fire calls against a live instance
can return HTTP 200 with an empty body instead of an error, which is
easy to mistake for success. A short fixed delay between calls, with a
slightly longer pause every N calls, is cheap insurance against this
class of failure. Report progress periodically on any run long enough
(dozens of calls) that the user might otherwise wonder if it's hung.

## Known limitations

- **No bulk Salary Slip creation endpoint.** Slips are created
  individually by employee+period; the "Create Salary Slip" UI button
  processes one employee at a time internally too. For batch runs
  where the **Payroll Entry** doctype is installed and configured,
  prefer it over individual scripting for more than a handful of
  employees — it handles slip creation, submission, and Journal Entry
  generation in one workflow.
- **Deduplication is not enforced at the API level.** Creating a
  second slip for the same employee+period does not fail — Frappe
  creates a second draft with an autoname suffix
  (`Sal Slip/None/00001`, `Sal Slip/None/00002`), which looks like a
  duplicate from a search filter. Search first (`fields=["docstatus"]`)
  and skip any employee+period that already has a submitted slip.
- **The autoname's literal `None`** (`Sal Slip/None/<NNNNN>`) is
  cosmetic — the payroll-frequency naming-series component isn't
  resolved at create time via REST — and doesn't affect functionality.
- **Submit does not disburse payment.** Salary Slip submission doesn't
  auto-create a Payment Entry or Journal Entry; that's a separate
  payroll-run/journalization step.
