# Qkeee Bot audit-trail doctype design (canonical)

Source of truth for the 4 `Qkeee Bot *` doctypes this skill creates. If
you're extending or fixing the audit-trail schema, start here — this file,
not the create-payloads in `scripts/doctype_defs.py`, is the design record;
sync the script from this doc, not the other way around.

Decision log / debate history for how this design was reached lives in
`qkeee-erp-skills-library-plan.md`'s "Bot Audit-Trail Doctype Design"
section. This file is the buildable spec that came out of it.

## Why 4 doctypes, no app

Every field/permission decision below assumes: doctypes are created at
runtime as `custom: 1` records via `POST /api/resource/DocType`, attached
to Frappe's built-in `Custom` module. No new Frappe app, no module folder,
no Python controller, no Server Script. `mutate_resource()` in
`qkeee-erp-frappe-core`'s connector already supports this with zero changes — a
`DocType` record is just another doctype to `create`; `DocType` itself is
simply not one of `qkeee_erp.mode`'s usual business-write targets.

Tradeoff accepted: no server-side `validate()`/`on_submit()` hooks. Every
integrity rule below (submittable lock, create-only permission, mandatory
fields) is enforced via doctype *metadata* (field `reqd`, `is_submittable`,
DocPerm flags) rather than code — sufficient for this design's needs, and
keeps the "no app" decision clean rather than half-committing to one via a
Server Script.

## Elevated credentials — read before running

Creating `DocType`/`Role` records requires System Manager–level permission.
**Run this skill's init flow with an elevated/admin API key, not the
shared `qkeee-erp-bot@<org>` service account** the persona skills use
day-to-day — that account should stay least-privilege (business-doctype
read/write only) and should *not* itself be able to create doctypes or
roles. Treat init as a one-time/occasional elevated-credential operation,
separate from steady-state bot traffic.

## The 4 doctypes

### 1. `Qkeee Bot Persona`

Master data. One row per installed `qkeee-erp-*` persona skill. Not a log
— unconditional, no debug-mode gating, no volume risk (single digits of
rows, ever).

| Field | Type | Notes |
| --- | --- | --- |
| `persona_code` | Data, unique, reqd | e.g. `qkeee-erp-hr-associate`; autoname `field:persona_code` |
| `persona_label` | Data, reqd | display name |
| `default_mode` | Select (`Read Only`/`Read Write`) | default `Read Only` |
| `non_negotiables` | Text | copied from the persona's SKILL.md, informational only |
| `active` | Check | default `1` |

Autoname: `field:persona_code`.

### 2. `Qkeee Bot Session`

One row per conversation. **Only created when `qkeee_erp.debug` is
`true`** (decision 10) — outside debug mode, a session is identified only
by the raw session-id string threaded through Audit Log rows (see
`Qkeee Bot Audit Log.session` below), with no corresponding doctype record.

| Field | Type | Notes |
| --- | --- | --- |
| `user` | Link → User, reqd | the human this session serves (`requested_by`), never the bot service account |
| `persona` | Link → Qkeee Bot Persona, reqd | |
| `environment_tag` | Data, reqd | connector's active-env tag at session start |
| `channel` | Select (`Web`/`Discord`/`Telegram`/`WhatsApp`/`Email`/`Slack`/`CLI`/`API`/`Other`) | the conversation surface this session came in on; `Other` is the escape hatch for a channel not yet enumerated here rather than blocking on a schema change |
| `channel_metadata` | Long Text (JSON) | free-form per-channel tracing detail — e.g. `{"chat_id": "..."}` (Discord/Telegram/WhatsApp), `{"message_id": "...", "thread_index": "..."}` (email headers), `{"channel_id": "...", "ts": "..."}` (Slack). Deliberately unstructured JSON rather than named columns per channel, so a new channel or a new attribute on an existing one never needs a schema change — see `channel`/`channel_metadata` in the Extension points section |
| `mode` | Select (`Read Only`/`Read Write`) | mode at session start; mode changes mid-session are logged as Messages, not backfilled here |
| `debug_mode` | Check | debug state at session start |
| `started_on` | Datetime | |
| `ended_on` | Datetime | |
| `status` | Select (`Active`/`Closed`/`Error`) | default `Active` |

Autoname: `hash` (random, unguessable session id — this value is what
Audit Log's `session` field carries as a plain string when debug is off).

Permission: create + read + write (to close out `status`/`ended_on`) for
role `Qkeee Bot`. No delete.

### 3. `Qkeee Bot Message`

One row per conversation turn. **Only created when `qkeee_erp.debug` is
`true`** (decision 10) — this is the doctype the debug gate exists
primarily to protect against bloat.

| Field | Type | Notes |
| --- | --- | --- |
| `session` | Link → Qkeee Bot Session, reqd | indexed |
| `speaker` | Select (`User`/`Bot Analysis`/`Bot Response`/`Bot Action`/`System`), reqd | splits bot analysis / response / action rather than one generic "Bot" role, per the original ask |
| `content` | Long Text, reqd | |
| `related_capability` | Data | e.g. "Journal Entry drafting" — ties the turn back to the persona's capability table |
| `in_reply_to` | Link → Qkeee Bot Message | self-link; the User message this turn answers. **Required for reconstructing conversations under interleaved/async turns** (decision 5) — do not rely on `creation` order alone once a harness allows a user to send a second message before the bot finishes responding to the first |
| `linked_audit_log` | Link → Qkeee Bot Audit Log | set only on Bot-Action messages that actually touched ERPNext. **Not present in the initial `DocType` create payload** — live-confirmed that Frappe rejects a Link field naming a not-yet-existing doctype at create time (`WrongOptionsDoctypeLinkError`), and Message is created before Audit Log. Added via a post-create `update` patch once Audit Log exists — see `scripts/doctype_defs.py`'s `DEFERRED_FIELD_PATCHES` and `init_bot.py`'s `ensure_deferred_fields()`. |

Autoname: `hash`.

Permission: **create-only** for role `Qkeee Bot` — `write: 0` after
insert (decision 9). Ordering for reconstruction: use Frappe's own
`creation` field, never a client-computed `sequence_no` (decision 6) —
client-side counters gap/collide under crash-and-retry.

### 4. `Qkeee Bot Audit Log`

One row per ERPNext record the bot reads/creates/updates/submits/
cancels/deletes. **The only doctype that is (mostly) unconditional** —
see the debug split below.

| Field | Type | Notes |
| --- | --- | --- |
| `session` | **Data**, not Link, reqd | raw session-id string. Deliberately not a Link to `Qkeee Bot Session` — that doctype may not exist outside debug mode (decision 10); a Data field stays populated as a correlator regardless, and still lets you group rows by conversation if a Session record does exist |
| `persona_code` | Data | denormalized from Persona, not resolved via a Session join, for the same reason |
| `environment_tag` | Data | denormalized, same reason |
| `channel` | Select, same options as `Qkeee Bot Session.channel` | denormalized from Session for the same reason a Session record may not exist outside debug mode; populated directly by the caller (the persona skill/connector knows the inbound channel regardless of whether Session logging is on) |
| `channel_metadata` | Long Text (JSON) | denormalized from Session, same reason; also settable directly per-row so a channel-specific tracing id (a chat id, an email `Message-Id` header, a WhatsApp `wamid`) is captured even when no Session exists to denormalize from |
| `triggering_message` | Link → Qkeee Bot Message | optional; only non-null when Message rows exist (debug on) |
| `action` | Select (`Read`/`Create`/`Update`/`Submit`/`Cancel`/`Delete`), reqd | |
| `reference_doctype` | Link → DocType, reqd | |
| `reference_name` | Dynamic Link (`reference_doctype`) | populated from the live write response — for `Create`, the doctype's assigned `name` as returned by ERPNext (never guessed/pre-computed); for `Update`/`Submit`/`Cancel`/`Delete`, the caller-supplied `name` as a guaranteed fallback if response parsing comes up empty. **Blank `reference_name` is expected on a `Failure` row** (the write never completed, nothing to reference) — `_audit_submit()` locks both `Success` and `Failure` rows the same way, so the list view's "Submitted" badge alone does not distinguish them; check `status`/`error_detail` on that row before treating a blank name as a bug. Blank `reference_name` on a `status = Success` row is NOT expected — `erp_client.py`'s `mutate_resource()` prints a `WARN` to stderr when this happens, since it means ERPNext's response shape didn't match what the connector assumes |
| `requested_by` | Link → User, reqd | denormalized from session for fast audit query without a join |
| `timestamp` | Datetime, reqd | indexed |
| `status` | Select (`Attempted`/`Success`/`Failure`), reqd | see two-phase logging below |
| `error_detail` | Small Text | on `Failure` |
| `payload_before` | Long Text (JSON) | Update only; null for Create/Read |
| `payload_after` | Long Text (JSON) | Create/Update only; null for Read |
| `field_diff` | Long Text (JSON) | Update only — `[{"fieldname","old","new"}, ...]`, computed by diffing `payload_before`/`payload_after` in `erp_client.py` before the row is written. No child table (decision 2) |
| `audit_comment_posted` | Check | whether the connector's best-effort audit Comment landed |
| `user_approved` | Select (`Not Required`/`Approved`/`Not Confirmed`), reqd | **decision 14.** Set by the caller, never inferred by the connector. `Not Required` only ever appears on `Read` rows (auto). Every `Create`/`Update`/`Submit`/`Cancel` row is either `Approved` (caller explicitly passed `user_approved=True` after a real confirm-stage exchange with the user) or `Not Confirmed` (caller didn't pass it). **This is a detection field, not a prevention gate** — the write still proceeds regardless of value; the point is to make a skipped-confirmation bug visible on a scan of Audit Log (`action != Read AND user_approved = "Not Confirmed"`), not to duplicate the `qkeee_erp.mode`/`requested_by` gates that already block unauthorized writes outright |
| `approval_note` | Small Text | Free text of what was confirmed (e.g. "user confirmed JE draft JE-0001, balanced, before submit"), populated by the calling skill's confirm-stage renderer where one exists (`render_je_draft.py`, `render_cancel_confirmation.py`, etc.) |

Autoname: `hash`. **Submittable** (`is_submittable: 1`) — once `Success`/
`Failure` is set and submitted, the row is locked (decision 9).

**Unconditional vs debug-gated split (decision 10):**
- `Create`/`Update`/`Submit`/`Cancel`/`Delete` rows — **always logged**,
  regardless of `qkeee_erp.debug`. This is the compliance-critical half
  ("write/update log" from the original ask) and stays low-to-moderate
  volume even for busy personas.
- `Read` rows — **debug-mode only.** Identified as the likely
  highest-volume source of the four doctypes (a read-heavy persona like
  MIS Analyst can generate far more Read calls per session than Message
  turns) — gating Message alone would not have solved the stated bloat
  concern.

**Two-phase write (decision 7) — the core integrity mechanism:**
1. Insert Audit Log row with `status = Attempted`, *before* calling
   `mutate_resource()`'s actual write.
2. Perform the real write.
3. Update the same row to `status = Success` (with `payload_after`/
   `field_diff` filled in) or `status = Failure` (with `error_detail`).

A crash between steps 1 and 3 leaves an orphaned `Attempted` row instead
of a silent, unaudited write — detectable and reconcilable, rather than
invisible. **Secondary cross-check:** enable `track_changes: 1` on every
doctype the bot writes to, so Frappe's built-in `Version` doctype
independently captures field diffs server-side; periodically reconcile
Version against Audit Log (same doctype + name + timestamp window) to
catch anything either logger missed.

**Infinite-loop guard (decision 11) — mandatory in `erp_client.py`:**

```python
AUDIT_EXEMPT_DOCTYPES = {
    "Qkeee Bot Session", "Qkeee Bot Message",
    "Qkeee Bot Audit Log", "Qkeee Bot Persona",
    "Comment",  # the best-effort audit-comment post itself
}
```

Checked before the audit-wrap step in `mutate_resource()`: if
`reference_doctype` is in this set, skip logging. Without it, logging an
Audit Log insert would recursively log itself forever; without `Comment`
specifically in the set, every audited write would double-log itself
(once for the record, once for the audit-comment `Comment` write that
documents it).

## Permission matrix

| Doctype | Role `Qkeee Bot` | Role `System Manager` |
| --- | --- | --- |
| Persona | read only (`read:1`, rest `0`) | full (managed via init/admin, not runtime bot traffic) |
| Session | create, read, write (`submit:0`, `delete:0`) | read only |
| Message | create, read (`write:0` — immutable after insert) | read only |
| Audit Log | create, read, write (to flip Attempted→Success/Failure), submit (`cancel:0`, `delete:0`) | read only |

No role gets `delete` on any of the four doctypes via the permission
model, including `System Manager` on `Qkeee Bot Persona` — master data
included, so a decommissioned persona row is disabled (the `active`
checkbox) rather than deleted, keeping history intact. Retention/cleanup
(if ever genuinely needed) is a deliberate out-of-band action (Frappe
`Log Settings` / bench console by a System Manager), not something
exposed through normal doctype permissions — keeps the audit trail
append-only by default.

## Bulk-operation guard (decision 12)

Not a doctype-schema item, but documented here since it's directly about
volume: when a persona skill detects a bulk-shaped request (import,
demo-data generation, any batch implying N-many creates) while
`qkeee_erp.debug` is `true`, it must prompt the user to disable debug
mode first, explaining that debug-mode logging multiplies roughly in
proportion to batch size and risks saturating the target instance. Soft
gate (warn-and-confirm) enforced at the persona's SKILL.md instruction
level, not a hard code path in the connector.

## Config key

`QKEEE_ERP_<TAG>_DEBUG` — OPTIONAL per-tag env var in this agent
profile's own `.env`, default `false` if unset (2026-08-18 retrofit;
originally a single global `metadata.hermes.config` key,
`qkeee_erp.debug`, shared across every tag — moved per-tag so a profile
juggling multiple environments can debug-log one and not another). Gates
Session + Message creation and Audit Log's Read rows, per the split
above. `qkeee_erp.active_env`/`qkeee_erp.mode` remain global
`metadata.hermes.config` keys; `QKEEE_ERP_<TAG>_REQUESTED_BY` moved
alongside `_DEBUG` for the same per-tag reason. See
`qkeee-erp-frappe-core/references/connector-reference.md`'s "Requester
attribution and debug are per-tag, not global" for the full rationale.

## Extension points

- Adding a 5th "always-logged, low-volume" action type: add to the
  `action` Select on Audit Log; no schema restructure needed.
- Adding a channel not yet in `channel`'s Select options: add the option
  (or use `Other` + put the real name in `channel_metadata`, e.g.
  `{"channel_name": "signal"}`, until a schema change lands). Any
  channel-specific tracing attribute (chat id, message id, thread id,
  email headers, future fields no one's thought of yet) goes in
  `channel_metadata` as JSON, never as a new named column — that's the
  whole point of modeling it as JSON instead of per-channel fields.
- Targeting a different ERP backend: same extension point as the rest of
  the library — only the connector layer (`erp_client.py` here) and this
  file's endpoint assumptions change; the doctype *shape* is Frappe-
  specific by nature (this design doesn't claim to be ERP-agnostic, unlike
  the persona skills' domain-knowledge layers).
- Re-enabling per-field child-table detail if `field_diff` JSON ever
  proves insufficient for some reporting need: reintroduce a child
  doctype at that point rather than pre-building it now (decision 2 —
  YAGNI'd deliberately).
