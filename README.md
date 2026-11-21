# Dutch Freelance Workbench

A small, local-only tool for one solo software freelancer in the Netherlands.
It answers one question:

> **Which freelance opportunity needs my attention next?**

You record opportunities (leads), log outreach and notes as a local journal,
keep one dated follow-up per lead, and the dashboard tells you what is overdue,
due today, upcoming, or has no follow-up yet. Nothing is ever sent anywhere:
"outreach" is a journal entry, not an email.

This is milestone **M1**. It runs on your own machine with Python's standard
library and SQLite. There is no account, no cloud service, no CDN, no package
download and no build step.

## What M1 includes

- **Settings and first use**: one workspace display label (1–120 characters).
  The UI is in English and the business timezone is fixed to `Europe/Amsterdam`.
  A saved label is required before you can add leads.
- **Manual lead capture and editing**: an opportunity label, the counterparty
  country (`NL`, `other`, `unknown`), the work mode (`remote`, `hybrid`,
  `onsite`, `unknown`), the source channel (`manual`, `direct`, `platform`,
  `referral`, `other`) and an optional source label. Duplicate labels are allowed.
- **Lead list**: search over lead and source labels, with filters for stage,
  country, work mode, source and follow-up bucket. Filters combine with AND and
  live in the URL, so Back returns to the same list.
- **Lead detail**: fields, stage, version, current follow-up and the activity
  history in recording order.
- **Stages** `new` → (optional) `researching` → `contacted`. A lead becomes
  `contacted` only by recording its first outreach, which must include a dated
  follow-up. `researching` also needs a dated follow-up.
- **Activities**: `outreach` and `note`, each with a business date that cannot
  be later than today in Amsterdam. Entries are never edited or deleted; add a
  later note to clarify a mistake.
- **One current follow-up per lead**: text (1–240 characters) and due date are
  saved together. Past due dates are allowed. Only a `new` lead may have none.
- **Dashboard**: the server's Amsterdam business date and four groups:
  **Overdue**, **Today**, **Upcoming** and **No action**. Each lead is in exactly
  one group. Groups are computed when you load the page, so after midnight in
  Amsterdam a refresh moves items without any database write.
- **Safe saves**: every save is one SQLite transaction with a version check,
  an idempotency key (a retried save is never applied twice, even after a
  restart) and a minimal audit row (command, record, resulting version, time,
  stage change). The audit holds no labels, journal text, keys or request bodies.

### Discover (after M1)

- **Discover** browses a small catalog of fictional opportunities that ships
  with the source (`workbench/catalog/fictional-opportunities.json`). Filter it
  by skill, engagement type (hourly contract, fixed scope, retainer),
  counterparty country and work mode. Filters combine with AND and live in the
  URL. Browsing never creates or changes anything.
- **Save as lead** turns an item into an ordinary `new` lead through the same
  validation as manual capture: the item title becomes the label, its country
  and work mode are copied, the source channel is `platform` and the source
  label is the item's organisation. The lead stores the item's stable catalog
  key, and the database allows one lead per key, so a retry, a second tab or two
  simultaneous saves return the existing lead instead of a duplicate. A saved
  item shows **Open lead**.
- If the catalog file cannot be loaded, Discover says so and links to manual
  lead capture; everything else keeps working.

Not included yet (planned as follow-up GitHub issues): closing/reopening
leads and later stages, versioned terms, project handoff, reports and CSV,
backup and restore, and invoicing.

## Prerequisites

- Python **3.12 or newer** (standard library only; nothing to install).
- A current web browser.

## Quickstart

```sh
git clone https://github.com/jortega0033/dutch-freelance-workbench.git
cd dutch-freelance-workbench
python3 -m workbench --data-dir data/main
```

On Windows, use `py -3.12` (or a `python` that is 3.12+) instead of `python3`.

The server prints where it is listening and where your data lives:

```
Dutch Freelance Workbench 0.1.0 (M1)
URL:      http://127.0.0.1:8000/
Database: /path/to/dutch-freelance-workbench/data/main/workbench.sqlite3
Press Ctrl+C to stop.
```

Open the URL, save a display label in **Settings**, then add your first lead.
Stop the server with Ctrl+C. Start it again with the same `--data-dir` and
everything you saved is still there.

Options:

| Option | Meaning |
| --- | --- |
| `--data-dir DIR` | Required. Folder for the database `workbench.sqlite3`. Created if missing. |
| `--port PORT` | Optional. Default `8000`. The server only ever binds to `127.0.0.1`. |

If the database file is corrupt or belongs to something else, the server
reports the problem, exits, and leaves the file untouched.

A database created by M1 (schema version 1) is upgraded in place on the first
start: one transaction adds the nullable `lead.catalog_key` column and its
unique index, and existing leads, history, follow-ups, audit rows and retry
records stay as they were. If the upgrade fails, it is rolled back and the file
keeps its old schema. The M1 release cannot open an upgraded file (it reports
an unsupported schema version and leaves the file unchanged). If you may want
to go back, stop the running app (Ctrl+C) and then copy its whole `--data-dir`
folder before starting this version. The server takes its port before it
touches the database, so a launch that fails because the port is busy leaves
an M1 database as it was.

## Your data

All state is one SQLite database (plus SQLite's `-wal`/`-shm` sidecar files
while it runs) inside the folder you pass as `--data-dir`. A normal launch
never creates sample records. Different folders are completely independent,
so keep your real work and any experiments apart:

```sh
python3 -m workbench --data-dir data/main             # your own workspace
python3 -m workbench --data-dir data/demo --port 8001 # a separate demo workspace
```

`data/` is in `.gitignore`. Use only fictional data in demo folders.

## A fictional sample workflow

Try this in a demo folder (`--data-dir data/demo --port 8001`):

1. **Settings**: save the display label `Fictional Studio`.
2. **Leads → Add a lead**: label `Fictional interface review`, country
   Netherlands, work mode Remote, source channel Direct, source label
   `Fictional referral contact`. The lead starts at **New**, version 1.
3. On the lead, **Record activity**: type Outreach, today's date, summary
   `Recorded a fictional introduction about an interface review.`, next action
   `Review the fictional reply` due tomorrow. The lead becomes **Contacted**,
   version 2, with one outreach in its history.
4. **Dashboard**: the lead is under **Upcoming**. Tomorrow it moves to **Today**,
   and the day after to **Overdue**, without you saving anything.
5. Add a **note** later: the stage and follow-up stay as they are. Use **Change
   follow-up** to replace the action without logging outreach.

## Local-only behaviour

- Binds to `127.0.0.1` only. Requests whose `Host` is not `127.0.0.1:<port>` or
  `localhost:<port>` are refused.
- The browser UI and the JSON API (`/api/v1/...`) are served from the same
  origin. There are no CORS headers. Writes need the same-origin `Origin`
  header and a per-process session token that the page fetches from
  `/api/v1/session`; a restart issues a new token and the page renews it.
- A strict Content Security Policy allows only the app's own scripts, styles
  and API. All user text is rendered as text, never as HTML.
- All assets (HTML, CSS, JavaScript modules) are local; the app makes no
  external requests. The access log records only method, path and status.
- Health check: `GET http://127.0.0.1:8000/api/v1/health`.

## Tests

The automated suite uses only the standard library. It starts the real HTTP
server against file-backed temporary databases (and real separate processes
for the restart tests):

```sh
python3 -m unittest discover -s tests -t .
```

It covers settings, lead capture and search, the stage and follow-up rules,
dashboard buckets, Amsterdam date boundaries and daylight-saving changes,
catalog browsing and duplicate-safe saving, the upgrade from an M1 database,
idempotent replays (also after a restart), stale-version conflicts, rollback
after injected failures, storage contention, and local request security.
The server takes its clock and a failure-injection hook as constructor
arguments; the fixed clock and scripted failures used by the tests live only in
`tests/support/`, and `python3 -m workbench` always uses the system clock and no
failure injection.

### Optional browser acceptance run

`tests/browser/acceptance.mjs` drives a real local Google Chrome through the
main workflow by keyboard, checks two-tab conflicts, a lost response, restarts,
Amsterdam date boundaries, narrow layouts, Discover (filters, Save as lead
from two tabs, Open lead, outreach, dashboard, unavailable catalog), and
verifies that the app makes no external requests while all non-loopback
traffic is blocked. It is test tooling
only and needs Node.js, a globally installed `playwright` package and Chrome:

```sh
NODE_PATH="$(npm root -g)" WORKBENCH_PYTHON=python3 node tests/browser/acceptance.mjs
```

It uses fresh temporary data folders and browser profiles and prints where it
wrote its JSON evidence.

## Project layout

```
workbench/__main__.py   command line: --data-dir, --port
workbench/server.py     HTTP server, routing, request checks, static files
workbench/leads.py      write commands; one save = one transaction (see its docstring)
workbench/db.py         schema, startup checks, BEGIN IMMEDIATE transactions, idempotency, audit
workbench/queries.py    dashboard, lead list and detail reads
workbench/discovery.py  the bundled fictional catalog: loading, validation, filters
workbench/catalog/      the catalog JSON file
workbench/validation.py strict request validation
workbench/dates.py      clock and Amsterdam business date
workbench/errors.py     the API error type
workbench/faults.py     no-op failure-injection hook (tests supply their own)
workbench/static/       HTML, CSS and native JavaScript modules
tests/                  unittest suites and test-only support code
```

To follow one Save button: `static/js/forms.js` (`attachSave`) →
`static/js/api.js` (`apiMutate`) → `server.py` route → `leads.run_mutation` →
`db.write_transaction`.

## License

MIT. See [LICENSE](LICENSE).
