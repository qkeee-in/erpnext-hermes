# qkeee-erp-mis-analyst connector reference (read-only-only copy)

This is this skill's copy of the `qkeee-erp` connector layer, based on
the canonical version in `qkeee-erp-core/references/connector-reference.md`.
This copy is deliberately trimmed to the read path only — no
`mutate_resource`, no `mutate` CLI subcommand, no write endpoints
documented — per the module plan's decision that this persona is
read-only always, independent of `qkeee_erp.mode`. If a future sync from
`qkeee-erp-core` reintroduces write logic into `scripts/erp_client.py`
here, that's a sync error, not an intended update — this skill's script
should never gain a write path.

**Not a mechanical copy — hand-rewritten.** `qkeee-erp-core`'s
`connector-reference.md` documents a full end-to-end round-trip validated
against a live instance (`<erp-instance>`), but that validation
covered the *original, untrimmed* `erp_client.py`. This skill's copy was
written by hand to omit the write path rather than mechanically diffed
from that file. Future syncs of read-path fixes from `qkeee-erp-core`
must be reapplied by hand to this file too — there's no tooling that
diffs the two copies automatically.

**Read path independently live-verified.** Ran
`erp_client.py --tag demo health` (confirmed `logged_in_as:
"Administrator"`), `erp_client.py --tag demo query "Module Def"`, and
`erp_client.py --tag demo report "Trial Balance"` against `<erp-instance>`
— `health_check()`, `query_resource()`, and `run_query_report()` all
confirmed working against real data (two real companies, "Enfasco Inc."
and "Qkeee LLP"). Done as a side effect of validating
`qkeee-erp-accounts-executive`'s connector copy the same session (temporary
API key/secret minted via session login +
`frappe.core.doctype.user.user.generate_keys`, same technique
`qkeee-erp-core`'s original validation used).

## Auth

ERPNext (Frappe framework) REST API, token auth:

```
Authorization: token <api_key>:<api_secret>
```

Keys are generated per ERPNext user via **User → API Access → Generate
Keys** in the ERPNext UI — an org-side onboarding step, not automated
here.

## Environment / tag model

Same tagged model as every other `qkeee-erp-*` skill:

| Variable | Purpose |
| --- | --- |
| `QKEEE_ERP_<TAG>_BASE_URL` | e.g. `https://org.erpnext.com` |
| `QKEEE_ERP_<TAG>_API_KEY` | API key for that site/user |
| `QKEEE_ERP_<TAG>_API_SECRET` | API secret for that site/user |

At install, only `QKEEE_ERP_DEFAULT_BASE_URL`/`_API_KEY`/`_API_SECRET`
are prompted for (tag `DEFAULT`). Adding a second/third environment (e.g.
a `qa` tag for rehearsing a report against test data) is a runtime
action — walk the user through naming a tag and setting its three vars,
then offer to switch `qkeee_erp.active_env`. Missing-var failures name
the exact variable, never a generic "auth failed."

## Endpoints used (read-only subset)

| Purpose | Method | Path |
| --- | --- | --- |
| Health check | GET | `/api/method/frappe.auth.get_logged_user` |
| Query a DocType | GET | `/api/resource/<DocType>?filters=...&fields=...&limit_page_length=...` |
| Run a built-in report | POST | `/api/method/frappe.desk.query_report.run` — body `{"report_name": "...", "filters": {...}}`. Read-only (runs a report, doesn't mutate data) despite being a POST; see `run_query_report()` in `erp_client.py` and `references/erpnext-accounting-docs.md` for the report-name → capability map. |

**Health check confirms connectivity + auth only, not query-time
permission.** A passing health check does not guarantee the
authenticated ERPNext user has read access to GL Entry, Account, Cost
Center, or any other report-relevant DocType — that's checked separately
by ERPNext on each query and can fail (403/`PermissionError`) even after
a clean health check. Surface a permission failure as its own distinct
error to the user ("connected, but this user lacks read access to GL
Entry — ask your ERPNext admin to grant it"), not folded into a generic
connectivity-error message.

`filters` is a JSON-encoded list of `[fieldname, operator, value]`
triples; `fields` is a JSON-encoded list of fieldnames. See
`docs.frappe.io/framework` for full REST query syntax and
`docs.frappe.io/erpnext` (Accounts module pages) for GL Entry / Journal
Entry / Account / Cost Center / Accounting Dimension field names —
confirm exact field lists at build/report-design time via `GET
/api/resource/DocType/GL Entry` (etc.) against the target instance rather
than assuming docs are current for that org's customizations.

## Query pagination

`query_resource()` requests `limit + 1` rows and trims to `limit`,
returning `{"data": [...], "has_more": bool, "limit": N}`. **This matters
more here than almost anywhere else in the library:** a GL drill-down or
trial balance that silently drops rows past the default limit produces a
report that looks complete but doesn't reconcile — exactly the failure
mode `render_report.py`'s reconciliation-check gate exists to catch.
Always check `has_more`; re-query with a higher `--limit` or tighter
filters (period, account range, cost center) rather than presenting a
truncated pull as final.

## The read-only gate — structural here, not config-driven

Every other persona skill's connector checks `qkeee_erp.mode` in code
before a write call. This skill's copy has no such check because it has
no write call to gate — `mutate_resource()` was not carried over into
this copy's `erp_client.py`. `qkeee_erp.mode` is still declared in this
skill's frontmatter for consistency with the rest of the library, but it
has no effect here: this skill never reads it to decide whether to write,
because writing isn't a code path that exists.

## Harness capability discovery

Before assuming this bundled `urllib`-based script is the only option,
check whether the host harness already exposes an HTTP-capable tool and
prefer that. Also check for an existing charting/HTML-report/artifact
capability in the harness before relying on `render_report.py`'s plain
HTML wrapper — prefer the harness-native one if richer. Degrade
gracefully to the bundled scripts if neither is discoverable; never
hard-fail because discovery itself isn't supported.

## CLI usage

```
python erp_client.py list-envs
python erp_client.py --tag qa health
python erp_client.py --tag qa query "GL Entry" --filters '[["account","=","Sales"],["posting_date","between",["2026-04-01","2026-04-30"]]]' --fields '["posting_date","debit","credit","voucher_no","cost_center"]' --limit 500
```

## Extension point

To target a different ERP backend, replace `scripts/erp_client.py` and
this file (here and in `qkeee-erp-core`, the source of truth). Nothing in
`references/domain-knowledge.md` or this skill's `SKILL.md` needs to
change — they're written to be ERP-agnostic in substance.

## Audit-trail retrofit

This skill has no write path, so only the read side applies:
`query_resource()`/`get_resource()`/`run_query_report()` carry an
opt-in `debug` kwarg (`qkeee_erp.debug`, default `false`) that logs a
`Read` row to the `Qkeee Bot Audit Log` doctype, best-effort — silently
no-ops if the target instance hasn't run `qkeee-erp-bot-init` yet. Given
this skill is report-driven and read-heavy, leaving debug on for a long
session is the single likeliest way to generate the highest Read-row
volume of any `qkeee-erp-*` skill — see `qkeee-erp-bot-init/references/
bot-doctypes-design.md` decision 10.
