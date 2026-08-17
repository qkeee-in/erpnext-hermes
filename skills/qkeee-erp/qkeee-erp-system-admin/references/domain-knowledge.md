# System administration domain knowledge (ERP-agnostic)

This is what a competent, security-conscious sysadmin knows about
running access control and system configuration for a business
application — independent of which ERP executes it. ERPNext specifics
(exact doctype/field names, endpoint behavior) live in
`erpnext-system-admin-docs.md` and `connector-reference.md`, not here.

## Least-privilege role design

Access should be granted by role, not by person, and each role should
be scoped to what a job function actually needs — not what's
convenient to assign. Two failure modes to watch for:

- **Role sprawl** — granting a broad role (e.g. an org-wide "Manager"
  role) because it's faster than figuring out the narrow one. This
  quietly inflates the blast radius of a single compromised account
  and makes later access review much harder ("does this person
  actually need all of that?" becomes unanswerable without archaeology).
- **Elevated/admin roles as a shortcut** — granting full administrative
  access to solve a narrow problem ("they need to edit one report, just
  give them System Manager"). The right question is always "what is
  the smallest role that covers the actual need," not "what role
  definitely won't block them."

A permission change should always be traceable to a stated business
reason, not just a request. "Why does this role need write access to
this doctype" should have an answer someone could defend in an audit,
not just "someone asked."

## The provisioning/deprovisioning lifecycle

Creating access and removing access are not symmetric operations in
practice, even though they're logical opposites:

- **Provisioning** (new user, new role grant) is usually low-stakes to
  get slightly wrong — over-provisioning is a security debt that can be
  found and fixed later. Still worth getting right the first time, but
  a mistake here is recoverable.
- **Deprovisioning** (disabling/removing a departed user's access,
  revoking a role that's no longer needed) is where sysadmin work
  actually protects the organization — a departed employee's still-
  active account is a live risk, not a paperwork gap. Deprovisioning
  should be prompt and complete: login access, not just "marked as
  left" in some other system.
- **Disable vs. delete** is a real distinction, not just two ways to
  say "remove." Disabling preserves the audit trail (who did what while
  they had access) and is reversible if the departure turns out to be
  temporary or the record was needed for reference. Deleting is
  appropriate only when the account should never have existed (a typo,
  a test account) — and even then, only if nothing else references it,
  since forcing a delete through broken references usually causes more
  damage than it prevents.

## Permission changes as configuration-with-consequences

A permission/role change is not "just data" the way a business
transaction's field update is — it changes what OTHER actions become
possible for potentially many people at once. This asymmetry is why
permission changes deserve more scrutiny than an equivalently-sized
business-data edit:

- **State the blast radius, not just the field.** "Grant write access"
  sounds small; "everyone with the Auditor role can now edit Contact
  records" is the actual consequence. A good confirmation step
  translates the technical change into who-can-now-do-what.
- **Removing access can also have unintended consequences** — a role
  might currently be relied on by an automation, a scheduled report, or
  a workflow approval step that isn't obviously connected to "read
  access on this doctype." Before revoking, it's worth asking what
  currently depends on the access being removed, even though this is
  often hard to know for certain.
- **A reset-to-defaults action is categorically different from a single
  grant/revoke** — it discards every customization ever made, for every
  role, not just the one being discussed. Treat "reset permissions for
  X" requests with extra suspicion about whether that's really what's
  wanted versus a narrower, targeted change.

## DocType/schema customization as change management

Adding a custom field or adjusting a form's behavior is a schema
change, and schema changes have a different risk profile than data
changes even when small:

- **A single field addition is usually safe** — additive, doesn't break
  existing data or reports. Still worth checking for a naming collision
  and confirming the field type matches the actual data it'll hold
  (e.g. don't use Data/Text for something that should validate as a
  Date or a Link).
- **Removing or renaming an existing field/property is much riskier** —
  reports, print formats, and integrations may reference it by name;
  removing it can silently break things elsewhere that aren't visible
  from the customization screen itself. This is why "simple" in this
  skill's capability set means "add one field" or "change one property
  value," not "remove or rename."
- **Customizations should be revisited over time.** An org that lets
  custom fields accumulate without ever reviewing whether they're still
  used ends up with a schema that's hard to reason about — this is a
  general software-maintenance truth, not ERPNext-specific.

## System health as a leading, not lagging, indicator

Checking scheduled/background job health, error logs, and
notification/integration configuration proactively (rather than only
when a user complains) is what separates "sysadmin" from "help desk."
A stopped scheduled job, or a spike in a particular error type, is
often the first visible sign of a problem that will otherwise surface
later as a business-process failure (an unpaid invoice reminder never
firing, a stock reorder never triggering) that's much harder to trace
back to its actual cause.

## Integration/webhook surface as an attack surface, not just a feature

Every configured outbound webhook or inbound integration is a
potential point of failure or compromise, not just automation.
Reviewing what's configured (where does data go, on what trigger, with
what credentials) periodically is a legitimate sysadmin task even
absent a specific incident — an org rarely has a single, up-to-date
mental inventory of every integration point unless someone maintains
one deliberately.
