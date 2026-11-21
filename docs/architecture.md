# Architecture and requirements baseline

Verified against `main` at `223da9a` (M1 plus Discover, schema version 2) on 2026-11-21.
The README describes behaviour for users; this file describes how the code is put together
and where the plan and the code differ. Function names are cited instead of line numbers
so the references survive edits. Re-verify a statement here before relying on it in a
later change, and update this file in the same pull request when a boundary moves.

Status words used below:

- **Implemented**: present in source and covered by a test or a run recorded here.
- **Planned**: described in a GitHub issue, no code yet.
- **Needs verification**: stated somewhere but not checked against source in this pass.

## Shape of the system (implemented)

One Python process, standard library only, serving a JSON API and a static browser client
from the same origin on `127.0.0.1`. State is one SQLite file. There is no build step, no
package manifest and no CI configuration in the repository.

The pattern is a layered modular monolith: an HTTP layer, transaction-script write
commands, plain read queries, and a storage module. The client is native ES modules with
a small regex router.

| Layer | Module | Owns | Imports (verified from the `import` lines) |
| --- | --- | --- | --- |
| Entry | `workbench/__main__.py` | CLI options, exit codes, startup banner | `db`, `server` |
| HTTP | `workbench/server.py` | Routing (`ROUTES`, `PAGE_ROUTES`), request checks, security headers, static files, read handlers `_read_*` | `dates`, `db`, `discovery`, `leads`, `queries`, `validation`, `errors`, `faults` |
| Write commands | `workbench/leads.py` | `run_mutation` and every command (`save_profile`, `create_lead`, `save_catalog_item`, `update_lead`, `add_activity`, `set_next_action`, `change_stage`) | `dates`, `db`, `discovery`, `queries`, `validation`, `errors` |
| Catalog | `workbench/discovery.py` | Loading, validating and filtering the bundled catalog JSON | `validation`, `errors` |
| Reads | `workbench/queries.py` | Dashboard, lead list, lead detail, bucket computation | `dates` |
| Validation | `workbench/validation.py` | Strict request parsing, vocabularies (`STAGES`, `COUNTRY_TAGS`, ...) | `dates`, `errors` |
| Storage | `workbench/db.py` | Schema, startup checks, upgrades, `Context`, `write_transaction`, idempotency and audit rows | `errors` |
| Leaves | `workbench/dates.py`, `errors.py`, `faults.py` | Clock and Amsterdam business date, `ApiError`, no-op fault hook | none |

Dependency direction is `__main__` -> `server` -> `leads` -> `{discovery, queries, validation, db}`
-> `{dates, errors}`. `db.py` and `queries.py` never import a command module; `db.Context.catalog`
is an untyped attribute so `db` does not import `discovery`. Keep it that way: a new domain
module may import `db`, `queries`, `validation`, `dates` and `errors`, and is wired into
`server.ROUTES`; storage and read modules must not import upward.

Client modules (`workbench/static/js/`): `app.js` (router and boot), `api.js` (session,
`apiGet`, `createPending`, `apiMutate`), `forms.js` (`attachSave`: one pending save per
click, retry with the same key and body), `dom.js` (element helpers; all user text is
set as text), and one module per screen in `views/`.

## Data ownership (implemented)

- **SQLite file** `workbench.sqlite3` inside `--data-dir`, WAL mode, `application_id`
  `0x44465742`, `user_version` 2. Tables: `profile` (single row), `lead`, `activity`,
  `audit`, `idempotency`. `lead.catalog_key` and the partial unique index
  `lead_catalog_key` come from `db.UPGRADES[2]`. A catalog save that finds an existing
  lead (`created: false`) writes no audit row.
- **Catalog** `workbench/catalog/fictional-opportunities.json` (format 1, 12 items):
  read-only, loaded lazily and cached by `discovery.Catalog`; a failed load is not cached.
- **Client state**: session token and `hasProfile` in the `api.js` module, filters in the
  URL, the last list URL in `sessionStorage`, and one pending save inside each
  `attachSave` closure. Nothing else is stored in the browser.

## Invariants future work must keep

| Invariant | Enforced in | Checked by |
| --- | --- | --- |
| One save is one transaction: idempotency lookup, command, audit row, stored response, then commit; any exception rolls everything back | `leads.run_mutation`, `db.write_transaction` (`BEGIN IMMEDIATE`) | `tests/test_faults_concurrency.py`, `tests/test_idempotency.py` |
| Optimistic concurrency: every command that changes an existing record checks the expected version and bumps it exactly once; a create starts at version 1 and `save_catalog_item` carries no version | `leads._load_lead`, `leads._update_lead`; `CHECK (version >= 1)` | `tests/test_idempotency.py`, `tests/test_workflow.py` |
| A retried save is never applied twice, also after a restart; a reused key with a different request is `409 KEY_REUSED` | `idempotency` table (rows are never deleted), `run_mutation` | `tests/test_idempotency.py`, `tests/test_process.py` |
| Audit rows hold command, entity id, resulting version, time and stage names only; never labels, text, keys or bodies | `db.insert_audit`, `audit` table has no free-text column | `tests/test_workflow.py` (`test_audit_holds_no_free_text`) |
| Stage and follow-up rules: only a `new` lead may have no follow-up; text and due date are set together | `lead` table `CHECK`s, `leads.change_stage`, `leads.add_activity`, `leads.set_next_action` | `tests/test_workflow.py`, `tests/test_validation.py` |
| Dates are Amsterdam business dates; activity dates cannot be in the future; dashboard buckets are computed at read time, never stored | `dates.business_date`, `validation.parse_activity`, `queries.bucket_for` | `tests/test_dates.py`, `tests/test_buckets_clock.py` |
| Local only: bind `127.0.0.1`; `Host` must be `127.0.0.1:<port>` or `localhost:<port>`; writes need a same-origin `Origin` and the per-process session token; no CORS headers | `server.create_server`, `Handler._check_host`, `_check_origin`, `_check_session` | `tests/test_security.py` |
| Strict Content Security Policy on every response; no external requests | `server.SECURITY_HEADERS`, `Handler._send` | `tests/test_security.py`, `tests/browser/acceptance.mjs` |
| User text is rendered as text; the client has no `innerHTML` | `static/js/dom.js` (`h`, `replaceContent`) | No automated test: confirmed by searching `workbench/static/` on 2026-11-21; the server side is covered by `test_markup_in_text_is_stored_and_returned_verbatim` in `tests/test_security.py` |
| The port is taken before the database is prepared, so a failed launch leaves the file untouched | `server.create_server` (`server_bind`, then `db.prepare_database`, then `server_activate`) | `tests/test_migration.py`, `tests/test_process.py` |
| A rejected or foreign database file is left byte-for-byte unchanged | `db._check_existing`, `db._check_without_leftovers` | `tests/test_process.py`, `tests/test_regressions.py` |
| Clock and fault hook are constructor arguments; the command line always uses the system clock and `NoFaults` | `server.create_server`, `db.Context`, `faults.NoFaults` | `tests/test_process.py` (`CliHygiene.test_main_uses_only_the_system_clock_and_no_faults`); the test doubles are `FixedClock` and `ScriptedFaults` in `tests/support/harness.py` |
| Standard library only at runtime; all assets are local | no dependency manifest exists | `tests/browser/acceptance.mjs` blocks non-loopback traffic |

## Workflow trace: Discover, filter, Save as lead, Open lead (implemented)

Observed on 2026-11-21 with `python -m workbench --data-dir <scratch> --port 18990`
(Python 3.13.14, Windows 11) and plain HTTP requests; results are listed under
"Observed run" below.

**Startup.** `__main__.main` calls `server.create_server`, which builds the socket,
binds it (`PortUnavailable` exits with code 3), calls `db.prepare_database` (create,
verify or upgrade; `DatabaseRejected` exits with code 2), attaches a `db.Context` holding
the clock, fault hook and `discovery.Catalog`, and only then starts listening.

**Page load.** `GET /discover` matches `server.PAGE_ROUTES` and returns
`static/index.html`, which loads `js/app.js` as a module. `boot()` fetches
`GET /api/v1/session` (token, business date, `has_profile`) and the router maps
`/discover` to `views/discover.js` `render`.

**Read path.** `discover.js` reads the filters from the URL (`currentFilters`), so the URL
is the filter state, and calls `apiGet('/api/v1/catalog?...')`. On the server
`Handler._pipeline` runs `_check_host`, finds the route and calls `server._read_catalog`:
`discovery.parse_filters` (unknown or repeated parameters and out-of-vocabulary values
are `422 VALIDATION_FAILED`), then `discovery.browse` inside `Context.read()` (a read
snapshot). The only SQL is `SELECT id, label, catalog_key FROM lead WHERE catalog_key IS
NOT NULL`, used to mark items that already have a lead. Nothing is written. The response
carries `facets`, `catalog_total`, `total` and `items[]` with `saved_lead` set or null.

**Write path.** "Save as lead" is a form wired with `forms.attachSave`. One click creates
one pending save (`api.createPending`: fixed body text `{}` and a fresh key) and sends
`POST /api/v1/catalog/items/<cat-key>/lead` through `api.apiMutate` with
`Content-Type`, `X-Workbench-Session` and `Idempotency-Key`. In `Handler._pipeline` the
mutation branch runs, in order, `_check_host`, `_check_origin`, `_check_session`,
`_parse_json_body`, `_idempotency_key`, `_find_route`, then
`leads.run_mutation(ctx, method, path, key, body, leads.save_catalog_item, <cat-key>)`.

Inside one `db.write_transaction`:

1. `db.find_idempotent`: an exact replay returns the stored status and body
   (`Idempotent-Replayed: true`); the same key with a different request is `409 KEY_REUSED`.
2. `leads.save_catalog_item`: `validation.parse_empty_body`; `ctx.catalog.get`
   (`503 CATALOG_UNAVAILABLE` or `404`); `_require_profile` (`409 PROFILE_REQUIRED`);
   `SELECT id FROM lead WHERE catalog_key = ?`. An existing lead returns
   `200 {lead, created: false}`. Otherwise `discovery.lead_body` turns the item into an
   ordinary create payload (`source_channel` `platform`, `source_label` = organisation),
   `validation.parse_lead_create` validates it, and `leads._insert_lead` inserts the lead
   (`stage` `new`, `version` 1, `catalog_key`) and one audit row (`lead.create`).
   The result is `201 {lead, created: true}`.
3. `db.store_idempotent` stores the exact response, `faults.hit("before_commit")` runs,
   then `COMMIT`.

**Back in the browser.** `onSuccess` in `discover.js` announces the outcome, reloads the
catalog and moves focus to the item's "Open lead" link (`/leads/<id>`), which the router
maps to `views/lead.js`.

**Edge cases.**

- *Second tab or concurrent save (new key).* The command finds the existing row under
  `BEGIN IMMEDIATE` and returns `200 created: false`; no lead and no audit row is added.
  The partial unique index `lead_catalog_key` is the backstop.
- *Lost response, retry with the same key.* The stored `201` body is replayed.
- *Catalog missing or malformed.* `GET` and `POST` return `503 CATALOG_UNAVAILABLE`; the
  view offers manual capture; a repaired file is picked up without a restart.
- *Contention beyond the busy timeout.* `503 STORAGE_BUSY` with `Retry-After`.

### Observed run (2026-11-21)

| Step | Result |
| --- | --- |
| `GET /api/v1/health` | `200 {"status": "ok", "schema_version": 2}` |
| `GET /discover` | `200 text/html` |
| `GET /api/v1/catalog?country=NL&mode=remote` | `catalog_total` 12, `total` 3 |
| `PUT /api/v1/profile` | `200` |
| `POST /api/v1/catalog/items/cat-0002-tulip-api-hardening/lead` | `201`, lead id 1, `created: true` |
| Same request, same key | `201` with `Idempotent-Replayed: true` |
| Same request, new key | `200`, `created: false` |
| `GET /api/v1/dashboard` | 1 lead under `no_action` |
| `GET /api/v1/health` with a foreign `Host` | `403` |

This is a runnable end-to-end prototype with a documented launch path (README
"Quickstart"), a repeatable demo (README "A fictional sample workflow"), validation and
persistence. The browser part of the trace was read from source, not driven in a browser,
in this pass; `tests/browser/acceptance.mjs` covers it and was not run (it needs a global
`playwright` package and Chrome).

## Requirements baseline and drift

There is no PRD file. Requirements live in two places:

1. `README.md` for shipped behaviour (M1 and Discover).
2. Release tracker [#18](https://github.com/jortega0033/dutch-freelance-workbench/issues/18)
   and its child issues for planned work (W1 to W4), plus the later backlog (#9 to #13, #15).

| Claim | Where stated | Status |
| --- | --- | --- |
| Schema version 2 is the baseline | #18 | Implemented (`db.SCHEMA_VERSION`, `UPGRADES[2]`) |
| Stages `new`, `researching`, `contacted`; `contacted` only through first outreach | README | Implemented (`validation.STAGES`, `leads.change_stage`, `leads.add_activity`) |
| Follow-up rules | README | Implemented (table `CHECK`s and commands) |
| Idempotent, versioned, audited saves | README | Implemented |
| Bind to `127.0.0.1`, Host/Origin/token checks, CSP, no external requests | README | Implemented |
| Port before database; in-place v1 to v2 upgrade with rollback | README | Implemented |
| Catalog of 12 fictional items, duplicate-safe save | README, #1 | Implemented |
| Save call chain `forms.js attachSave` -> `api.js apiMutate` -> `server.py` route -> `leads.run_mutation` -> `db.write_transaction` | README | Implemented |
| "Save as lead creates a lead through the existing create command" | #1 | Partially: it has its own route and command (`leads.save_catalog_item`) that reuses `validation.parse_lead_create` and `leads._insert_lead`, not `leads.create_lead` |
| Startup banner says `(M1)` | `__main__.main`, README sample output | Stale label: the build also contains Discover and schema 2. Cosmetic; `tests/test_process.py` asserts the banner text and `tests/support/serve_fixed_clock.py` prints the same label, so change them together |
| Terms revisions, `in_discussion`, conditional interest, won, closed, reopen | #2, #8 | Planned (no stage, table or route exists) |
| Projects, milestones, deliverables, actual minutes | #3, #19 | Planned |
| Invoice drafts, confirmation, receipts ledger | #6, #14 | Planned |
| Reports, CSV export, backup, restore, restore validation | #4, #16, #5, #17 | Planned |
| Audit viewer, diagnostics and seed, conflict UX, timer, credits, checklists | #10, #12, #11, #13, #15, #9 | Planned, outside W1 to W4 |
| Stale stage action and partial-date behaviour described in #11 | #11 | Needs verification (the client code was not exercised in a browser in this pass) |

Terms in the issues that point at material not in the repository: `P09` (in #10) and the
"four-session gate" wording (in #4 and #5) refer to an earlier planning document that was
never committed. The `T01` to `T15` identifiers are test-scenario labels and do exist, as
class names and docstrings in `tests/` (for example `T15GitIgnore` in
`tests/test_regressions.py`). Treat the issue text itself as the requirement.

## Extension seams for planned work

The Discover change (commits `28f5c34` and `b99c366`) is the worked example for adding a
feature. The steps it followed:

1. **Schema.** Add `db.UPGRADES[n]` and bump `db.SCHEMA_VERSION`. A fresh database runs
   `SCHEMA_STATEMENTS` (the v1 schema) and then every upgrade, so do not edit
   `SCHEMA_STATEMENTS`: `tests/test_migration.py` builds its v1 fixture from it. Update
   `db.REQUIRED_TABLES` when adding tables, and the tests that assert the version number
   (`tests/test_migration.py`, `tests/test_process.py`). `tests/support/harness.py`
   `TABLES` feeds `table_counts` and is also used by the version-1 upgrade
   test, so give that test its own table list before extending `TABLES`. Each later
   ticket needs a database at the previous schema version for its upgrade test; no
   helper for that exists yet (#2 is asked to add one).
2. **Write command.** Add `command(conn, ctx, target, body)` returning `(status, payload)`
   with a `validation.parse_*` function, register it in `server.ROUTES` with the write
   flag, and let `run_mutation` supply the transaction, idempotency and fault hook.
3. **Read endpoint.** Add a `_read_*` handler using `ctx.read()` and a `ROUTES` entry; a
   new page URL also needs a `PAGE_ROUTES` entry.
4. **Client view.** Add `static/js/views/<name>.js` exporting `render(main, ctx)`, a route
   in `app.js`, a nav item in `index.html`; use `apiGet` and `forms.attachSave`.
5. **Tests.** Subclass `WorkbenchTestCase`; use `FixedClock`, `ScriptedFaults.arm(...)`,
   `Client.mutate`, `table_counts` and `snapshot` to prove there are no partial writes;
   add a browser journey to `tests/browser/acceptance.mjs`.

### Known constraint for W1 (needs a design decision in #2)

The allowed stages are a `CHECK` inside the v1 `CREATE TABLE lead` statement, and SQLite
cannot alter a `CHECK`. The same table also carries
`CHECK (stage = 'new' OR next_action_text IS NOT NULL)`, which #8 must relax because won
and closed leads have no follow-up. The current upgrade path (`db._upgrade`) only
supports additive statements ("upgrades only add nullable columns and indexes"). Adding
`in_discussion` (#2) and the later stages (#8) therefore needs a table-rebuild upgrade
inside the single upgrade transaction, or a different way of constraining stages and
follow-ups. Facts a rebuild must respect:

- Create the new table, copy the rows with the same ids, drop the old table, rename.
- Recreate `lead_catalog_key` (it is an index on `lead` and is dropped with it). Do not
  recreate `activity_lead`: it is an index on `activity` and survives.
- `activity.lead_id` references `lead(id)`. `db._upgrade` opens a plain connection with
  foreign keys off, which is what makes dropping `lead` possible; SQLite cannot change
  that pragma inside a transaction, so keep it off there and run
  `PRAGMA foreign_key_check` before `COMMIT`.
- Keep the `lead` row in `sqlite_sequence` so new ids continue after the old ones.
- `tests/test_migration.py` builds its version-1 fixture from `SCHEMA_STATEMENTS` and
  asserts row counts over `TABLES` from `tests/support/harness.py`, so adding a table to
  `TABLES` breaks that test unless it gets its own fixed list of version-1 tables.

If the first rebuild relaxes both constraints (or moves the stage and follow-up rules out
of the table into the commands plus tests), #8 needs no second rebuild. This is the
largest technical risk in W1 and is called out in the estimate for #2.

Other seams that are not yet general and will need to be widened by the first ticket that
uses them: `run_mutation` passes a single path id (`lead_id`) to a command, so nested
resources such as a revision or a deliverable need a routing decision for writes (read
handlers receive the whole regex match and are not limited); all write commands
live in `leads.py`, so a new aggregate should get its own module that reuses
`run_mutation` rather than growing that file; `audit.entity_type` is free text and already
supports new entity types.
