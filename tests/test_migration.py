"""Issue #1: an M1 (schema v1) database upgrades to v2 with every row unchanged."""

import json
import socket
import sqlite3
from pathlib import Path

from tests.support.harness import TABLES, RunningServer, WorkbenchTestCase, db_file, db_query, new_key
from tests.test_process import ProcessTestCase, run_cli
from tests.support.harness import free_port
from workbench import db
from workbench.server import PortUnavailable, create_server

V1_LEAD_COLUMNS = ("id, label, country_tag, work_mode, source_channel, source_label, stage, version,"
                   " next_action_text, next_action_due, created_at_utc, updated_at_utc")


def _strip_catalog_key(value):
    if isinstance(value, dict):
        return {k: _strip_catalog_key(v) for k, v in value.items() if k != "catalog_key"}
    if isinstance(value, list):
        return [_strip_catalog_key(v) for v in value]
    return value


def make_v1_database(source_dir, target_dir):
    """Write a schema-v1 (M1) database holding the rows of source_dir.

    The rows were produced by the real API; the file gets exactly the M1
    schema (db.SCHEMA_STATEMENTS, unchanged since M1), M1's pragmas and WAL
    mode, and no catalog_key anywhere, including in stored responses.
    """
    target_dir.mkdir(parents=True)
    target = db_file(target_dir)
    conn = sqlite3.connect(str(target), isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN")
        for statement in db.SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.execute(f"PRAGMA application_id = {db.APPLICATION_ID}")
        conn.execute("PRAGMA user_version = 1")
        conn.execute("ATTACH DATABASE ? AS src", (str(db_file(source_dir)),))
        conn.execute(f"INSERT INTO main.lead ({V1_LEAD_COLUMNS}) SELECT {V1_LEAD_COLUMNS} FROM src.lead")
        for table in ("profile", "activity", "audit"):
            conn.execute(f"INSERT INTO main.{table} SELECT * FROM src.{table}")
        for row in conn.execute("SELECT * FROM src.idempotency").fetchall():
            row = list(row)
            row[5] = json.dumps(_strip_catalog_key(json.loads(row[5])), separators=(",", ":"))
            conn.execute("INSERT INTO main.idempotency VALUES (?, ?, ?, ?, ?, ?, ?)", row)
        conn.execute("DELETE FROM main.sqlite_sequence")
        conn.execute("INSERT INTO main.sqlite_sequence SELECT * FROM src.sqlite_sequence")
        conn.execute("COMMIT")
        conn.execute("DETACH DATABASE src")
    finally:
        conn.close()
    return target


def table_rows(data_dir):
    rows = {}
    for table in TABLES:
        columns = V1_LEAD_COLUMNS if table == "lead" else "*"
        order = "key" if table == "idempotency" else "id"
        rows[table] = db_query(data_dir, f"SELECT {columns} FROM {table} ORDER BY {order}")
    rows["sqlite_sequence"] = db_query(data_dir, "SELECT * FROM sqlite_sequence ORDER BY name")
    return rows


def pragma(data_dir, name):
    return db_query(data_dir, f"PRAGMA {name}")[0][0]


class UpgradeFromM1(WorkbenchTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.data_dir.parent / "source"
        source = RunningServer(self.source)
        self.addCleanup(source.stop)
        c = source.client
        self.assertEqual(c.put_profile("Fictional Studio").status, 200)
        a = c.create_lead("Fictional contacted lead", source_channel="direct", source_label="Fictional contact")
        a = a.json()["lead"]["id"]
        b = c.create_lead("Fictional researching lead", "other", "hybrid").json()["lead"]["id"]
        c.create_lead("Fictional new lead", "unknown", "unknown")
        self.outreach_key = new_key()
        self.outreach = c.outreach(a, 1, "Review the fictional reply", "2030-04-08", key=self.outreach_key)
        self.assertEqual(self.outreach.status, 201)
        self.assertEqual(c.activity(a, 2, summary="A later fictional note").status, 201)
        self.assertEqual(c.mutate("POST", f"/api/v1/leads/{b}/stage", {"version": 1, "stage": "researching",
                                  "next_action": {"text": "Read the fictional brief", "due_date": "2030-04-10"}})
                         .status, 200)
        self.assertEqual(c.put_action(b, 2, "Read the fictional brief again", "2030-04-12").status, 200)
        self.api_before = {
            "dashboard": c.get("/api/v1/dashboard").json(),
            "leads": [_strip_catalog_key(c.lead(i)) for i in (1, 2, 3)],
        }
        source.stop()
        self.v1 = self.data_dir
        make_v1_database(self.source, self.v1)
        self.assertEqual((pragma(self.v1, "user_version"), pragma(self.v1, "application_id")), (1, db.APPLICATION_ID))
        self.rows_before = table_rows(self.v1)
        self.assertEqual({t: len(self.rows_before[t]) for t in TABLES},
                         {"profile": 1, "lead": 3, "activity": 2, "audit": 8, "idempotency": 8})

    def test_upgrade_keeps_every_row_and_adds_the_catalog_link(self):
        running = self.start()
        self.assertEqual(pragma(self.v1, "user_version"), 2)
        self.assertEqual(table_rows(self.v1), self.rows_before)
        self.assertEqual(db_query(self.v1, "SELECT DISTINCT catalog_key FROM lead"), [(None,)])
        self.assertEqual(db_query(self.v1, "SELECT name FROM sqlite_master WHERE name = 'lead_catalog_key'"),
                         [("lead_catalog_key",)])
        # the API shows the same leads, history, follow-ups and dashboard as before
        c = running.client
        self.assertEqual(c.get("/api/v1/dashboard").json(), self.api_before["dashboard"])
        leads = [c.lead(i) for i in (1, 2, 3)]
        self.assertEqual([lead["lead"]["catalog_key"] for lead in leads], [None, None, None])
        self.assertEqual([_strip_catalog_key(lead) for lead in leads], self.api_before["leads"])
        self.assertEqual(c.get("/api/v1/catalog").json()["items"][0]["saved_lead"], None)

    def test_upgraded_schema_matches_a_fresh_database(self):
        self.start()
        fresh = self.data_dir.parent / "fresh"
        self.start(fresh)

        def schema(data_dir):
            return db_query(data_dir, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name")

        self.assertEqual(schema(self.v1), schema(fresh))

    def test_old_retry_still_replays_after_the_upgrade(self):
        c = self.start().client
        counts = self.counts()
        lead_id = self.outreach.json()["lead"]["id"]
        again = c.outreach(lead_id, 1, "Review the fictional reply", "2030-04-08", key=self.outreach_key)
        self.assertEqual(again.status, 201)
        self.assertEqual(again.headers.get("idempotent-replayed"), "true")
        self.assertEqual(again.json(), _strip_catalog_key(self.outreach.json()))
        self.assertEqual(self.counts(), counts)

    def test_upgraded_database_accepts_new_work(self):
        c = self.start().client
        saved = c.mutate("POST", "/api/v1/catalog/items/cat-0001-polder-parcel-portal/lead", {})
        self.assertEqual(saved.status, 201)
        self.assertEqual(saved.json()["lead"]["id"], 4)
        self.assertEqual(c.outreach(1, 3, "Another fictional follow-up", "2030-04-20").status, 201)
        self.assertEqual(table_rows(self.v1)["activity"][:2], self.rows_before["activity"])

    def test_upgrade_runs_once(self):
        self.start().stop()
        after_first = table_rows(self.v1)
        schema = db_query(self.v1, "SELECT sql FROM sqlite_master ORDER BY name")
        self.start()
        self.assertEqual(table_rows(self.v1), after_first)
        self.assertEqual(db_query(self.v1, "SELECT sql FROM sqlite_master ORDER BY name"), schema)

    def test_failed_upgrade_rolls_back_and_leaves_the_file_unchanged(self):
        # a v1 file that already has a conflicting column cannot be upgraded
        conn = sqlite3.connect(str(db_file(self.v1)))
        conn.execute("ALTER TABLE lead ADD COLUMN catalog_key TEXT")
        conn.commit()
        conn.close()
        before = table_rows(self.v1)
        schema = db_query(self.v1, "SELECT sql FROM sqlite_master ORDER BY name")
        with self.assertRaises(db.DatabaseRejected):
            self.start()
        self.assertEqual(pragma(self.v1, "user_version"), 1)
        self.assertEqual(table_rows(self.v1), before)
        self.assertEqual(db_query(self.v1, "SELECT sql FROM sqlite_master ORDER BY name"), schema)


class UpgradeFromTheCommandLine(ProcessTestCase):
    def test_cli_upgrades_and_reports_a_failed_upgrade(self):
        source = self.root / "source"
        server = RunningServer(source)
        self.assertEqual(server.client.put_profile().status, 200)
        self.assertEqual(server.client.create_lead("Fictional lead").status, 201)
        server.stop()

        good = self.root / "good"
        make_v1_database(source, good)
        rows = table_rows(good)
        process = self.launch(good)
        self.assertEqual(process.client().get("/api/v1/leads").json()["total"], 1)
        process.stop()
        self.assertEqual(pragma(good, "user_version"), 2)
        self.assertEqual(table_rows(good), rows)

        bad = self.root / "bad"
        path = make_v1_database(source, bad)
        conn = sqlite3.connect(str(path))
        conn.execute("ALTER TABLE lead ADD COLUMN catalog_key TEXT")
        conn.commit()
        conn.close()
        before = Path(path).read_bytes()
        result = run_cli("--data-dir", str(bad), "--port", str(free_port()))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("could not be upgraded", result.stderr)
        self.assertIn("left unchanged", result.stderr)
        self.assertEqual(Path(path).read_bytes(), before)


class StartupOrder(ProcessTestCase):
    """PR #7 review: the port is taken before the database is prepared, so a
    launch that cannot listen never upgrades (or otherwise touches) the file,
    and a launch whose database is rejected gives the port back."""

    def v1_copy(self, name):
        source = self.root / "source"
        if not source.exists():
            server = RunningServer(source)
            client = server.client
            self.assertEqual(client.put_profile().status, 200)
            lead = client.create_lead("Fictional lead").json()["lead"]["id"]
            self.assertEqual(client.outreach(lead, 1, "Fictional follow-up", "2030-04-12").status, 201)
            server.stop()
        return make_v1_database(source, self.root / name)

    def assert_port_free(self, port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))  # raises if anything still holds the port

    def test_port_in_use_leaves_a_v1_database_untouched(self):
        path = self.v1_copy("busy")
        before = path.read_bytes()
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            port = blocker.getsockname()[1]
            result = run_cli("--data-dir", str(path.parent), "--port", str(port))
            with self.assertRaises(PortUnavailable):
                create_server(path.parent, port, quiet=True)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("Is another copy already running?", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(pragma(path.parent, "user_version"), 1)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["workbench.sqlite3"], "no sidecar files")

    def test_rejected_database_releases_the_port(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        conn = sqlite3.connect(str(db_file(foreign)))
        conn.execute("CREATE TABLE unrelated (x)")
        conn.commit()
        conn.close()
        conflicting = self.v1_copy("conflicting").parent
        conn = sqlite3.connect(str(db_file(conflicting)))
        conn.execute("ALTER TABLE lead ADD COLUMN catalog_key TEXT")  # makes the upgrade fail
        conn.commit()
        conn.close()
        for data_dir in (foreign, conflicting):
            with self.subTest(data_dir=data_dir.name):
                port = free_port()
                with self.assertRaises(db.DatabaseRejected):
                    create_server(data_dir, port, quiet=True)
                self.assert_port_free(port)
                port = free_port()
                result = run_cli("--data-dir", str(data_dir), "--port", str(port))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assert_port_free(port)

    def test_launch_with_a_free_port_upgrades_and_keeps_the_rows(self):
        path = self.v1_copy("ok")
        rows = table_rows(path.parent)
        process = self.launch(path.parent)
        self.assertEqual(process.client().get("/api/v1/leads").json()["total"], 1)
        process.stop()
        self.assertEqual(pragma(path.parent, "user_version"), 2)
        self.assertEqual(table_rows(path.parent), rows)
        self.assertEqual(rows["activity"][0][2], "outreach")
        self.assertEqual(len(rows["idempotency"]), 3)
