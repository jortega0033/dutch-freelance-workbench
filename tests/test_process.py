"""T09 real restarts of `python -m workbench` as a subprocess, startup failures, CLI hygiene."""

import datetime as dt
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.harness import REPO_ROOT, ServerProcess, free_port, table_counts, db_query
from workbench import dates


def run_cli(*args, timeout=30):
    return subprocess.run([sys.executable, "-m", "workbench", *args], cwd=str(REPO_ROOT),
                          capture_output=True, text=True, timeout=timeout)


def same_path(a, b):
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


class ProcessTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def launch(self, data_dir):
        process = ServerProcess(data_dir)
        self.addCleanup(process.stop)
        return process.wait_ready()


class T09RealRestart(ProcessTestCase):
    def test_data_survives_process_restart_and_other_dir_is_independent(self):
        data_dir = self.root / "data"
        today = dates.business_date(dates.SystemClock().now_utc())  # the real Amsterdam today
        tomorrow = today + dt.timedelta(days=1)

        first = self.launch(data_dir)
        client = first.client()
        self.assertEqual(client.put_profile("Fictional Freelancer").status, 200)
        lead_id = client.create_lead("Restart Fixture Co", source_label="Fixture Board").json()["lead"]["id"]
        response = client.outreach(lead_id, 1, text="Check the fictional reply", due=tomorrow.isoformat(),
                                   business_date=today.isoformat(), summary="Fictional outreach before restart")
        self.assertEqual(response.status, 201, response.raw)
        note = client.activity(lead_id, 2, "note", today.isoformat(), "A note before restart")
        self.assertEqual(note.status, 201)
        before = client.lead(lead_id)
        first_token = client.token
        first.stop()

        second = self.launch(data_dir)
        client = second.client()
        self.assertNotEqual(client.token, first_token)
        after = client.lead(lead_id)
        self.assertEqual(after, before)
        self.assertEqual(after["lead"]["version"], 3)
        self.assertEqual(after["lead"]["stage"], "contacted")
        self.assertEqual(after["lead"]["next_action"], {"text": "Check the fictional reply", "due_date": tomorrow.isoformat()})
        self.assertEqual(after["lead"]["bucket"], "upcoming")
        self.assertEqual([a["kind"] for a in after["activities"]], ["outreach", "note"])
        self.assertEqual(after["activities"][0]["summary"], "Fictional outreach before restart")
        self.assertEqual(after["activities"][0]["business_date"], today.isoformat())
        self.assertEqual(client.get("/api/v1/profile").json()["profile"]["display_label"], "Fictional Freelancer")
        dashboard = client.get("/api/v1/dashboard").json()
        self.assertEqual(dashboard["counts"]["upcoming"], 1)
        self.assertEqual(dashboard["business_date"], today.isoformat())

        audit = db_query(data_dir, "SELECT command_type, entity_type, entity_id, resulting_version, from_stage, to_stage"
                         " FROM audit ORDER BY id")
        self.assertEqual(audit, [
            ("profile.save", "profile", 1, 1, None, None),
            ("lead.create", "lead", lead_id, 1, None, "new"),
            ("activity.outreach", "lead", lead_id, 2, "new", "contacted"),
            ("activity.note", "lead", lead_id, 3, None, None),
        ])

        # startup banner: URL and absolute database path
        self.assertIn("Dutch Freelance Workbench 0.1.0 (M1)", second.output)
        self.assertIn(f"URL:      http://127.0.0.1:{second.port}/", second.output)
        database_line = next(line for line in second.stdout_lines if line.startswith("Database:"))
        shown = database_line.split(":", 1)[1].strip()
        self.assertTrue(os.path.isabs(shown))
        self.assertTrue(same_path(shown, data_dir / "workbench.sqlite3"))
        self.assertIn("Press Ctrl+C to stop.", second.output)

        # a separate data dir sees nothing
        other = self.launch(self.root / "other")
        other_client = other.client()
        self.assertEqual(other_client.get("/api/v1/profile").json(), {"profile": None})
        self.assertEqual(other_client.get("/api/v1/leads").json()["total"], 0)
        self.assert_error_code(other_client.get(f"/api/v1/leads/{lead_id}"), 404)

        # replay across a real process restart
        second.stop()
        third = self.launch(data_dir)
        again = third.client().outreach(lead_id, 1, text="Check the fictional reply", due=tomorrow.isoformat(),
                                        business_date=today.isoformat(), summary="never stored", key="unused-key-0000000001")
        self.assertEqual(again.status, 409)  # stale: version moved on, different key

    def assert_error_code(self, response, status):
        self.assertEqual(response.status, status)

    def test_default_port_flag_is_optional_and_port_zero_picks_a_free_port(self):
        process = ServerProcess(self.root / "zero", port=0)
        self.addCleanup(process.stop)
        process.wait_ready()
        match = re.search(r"URL:\s+http://127\.0\.0\.1:(\d+)/", process.output)
        self.assertIsNotNone(match)
        self.assertNotEqual(match.group(1), "0")


class T09StartupFailures(ProcessTestCase):
    def check_rejected(self, data_dir, make_file):
        data_dir.mkdir(parents=True)
        path = data_dir / "workbench.sqlite3"
        make_file(path)
        before = path.read_bytes()
        result = run_cli("--data-dir", str(data_dir), "--port", str(free_port()))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(result.stderr.startswith("Error: "), result.stderr)
        self.assertIn("The file was left unchanged", result.stderr)
        self.assertIn("workbench.sqlite3", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in data_dir.iterdir()), ["workbench.sqlite3"], "no sidecar files")

    def test_corrupt_file(self):
        self.check_rejected(self.root / "corrupt", lambda path: path.write_bytes(os.urandom(8192)))

    def test_text_file_that_is_not_a_database(self):
        self.check_rejected(self.root / "text", lambda path: path.write_text("hello, I am not a database\n" * 200))

    def test_incompatible_sqlite_file_with_unrelated_table(self):
        def make(path):
            conn = sqlite3.connect(str(path))
            conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO unrelated (name) VALUES ('keep me')")
            conn.commit()
            conn.close()
        self.check_rejected(self.root / "unrelated", make)

    def test_unknown_schema_version(self):
        def make(path):
            conn = sqlite3.connect(str(path))
            conn.execute("CREATE TABLE profile (id INTEGER)")
            conn.execute("PRAGMA application_id = 1145459522")
            conn.execute("PRAGMA user_version = 99")
            conn.commit()
            conn.close()
        self.check_rejected(self.root / "future", make)

    def test_right_application_id_but_missing_tables(self):
        def make(path):
            conn = sqlite3.connect(str(path))
            conn.execute("CREATE TABLE profile (id INTEGER)")
            conn.execute("PRAGMA application_id = 1145459522")
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
            conn.close()
        self.check_rejected(self.root / "partial", make)

    def test_port_in_use(self):
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            port = blocker.getsockname()[1]
            result = run_cli("--data-dir", str(self.root / "busy"), "--port", str(port))
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("Error:", result.stderr)
        self.assertIn(str(port), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_second_server_on_same_port_fails(self):
        first = self.launch(self.root / "one")
        result = run_cli("--data-dir", str(self.root / "two"), "--port", str(first.port))
        self.assertEqual(result.returncode, 3, result.stderr)
        first.client().get("/api/v1/health")  # the first one is unharmed

    def test_missing_data_dir_option_is_a_usage_error(self):
        result = run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("--data-dir", result.stderr)

    def test_empty_directory_is_initialised_with_no_sample_data(self):
        process = self.launch(self.root / "fresh")
        client = process.client()
        self.assertEqual(client.get("/api/v1/leads").json()["total"], 0)
        process.stop()
        counts = table_counts(self.root / "fresh")
        self.assertEqual(counts, {"profile": 0, "lead": 0, "activity": 0, "audit": 0, "idempotency": 0})
        conn = sqlite3.connect(str(self.root / "fresh" / "workbench.sqlite3"))
        try:
            self.assertEqual(conn.execute("PRAGMA application_id").fetchone()[0], 1145459522)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            conn.close()

    def test_valid_but_blank_sqlite_file_is_adopted(self):
        data_dir = self.root / "blank"
        data_dir.mkdir()
        sqlite3.connect(str(data_dir / "workbench.sqlite3")).close()  # zero-byte file
        process = self.launch(data_dir)
        self.assertEqual(process.client().get("/api/v1/health").status, 200)


class CliHygiene(unittest.TestCase):
    def test_help_mentions_only_data_dir_and_port(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        flags = set(re.findall(r"--[a-z][a-z-]*", result.stdout))
        self.assertEqual(flags, {"--help", "--data-dir", "--port"})
        for word in ("clock", "faults", "sample", "fixed", "--now", "--host"):
            self.assertNotIn(word, result.stdout.lower())

    def test_unknown_options_are_rejected(self):
        for option in ("--now", "--clock", "--fixed-clock", "--faults", "--host", "--sample-data"):
            with self.subTest(option):
                result = run_cli("--data-dir", "unused", option, "x")
                self.assertEqual(result.returncode, 2)
                self.assertIn("unrecognized arguments", result.stderr)

    def test_production_package_never_imports_tests_or_reads_environment(self):
        for source in sorted((REPO_ROOT / "workbench").glob("*.py")):
            text = source.read_text(encoding="utf-8")
            with self.subTest(source.name):
                self.assertIsNone(re.search(r"^\s*(from|import)\s+tests\b", text, re.M))
                self.assertNotIn("serve_fixed_clock", text)
                self.assertNotIn("FixedClock", text)
                self.assertNotIn("os.environ", text)
                self.assertNotIn("getenv", text)

    def test_main_uses_only_the_system_clock_and_no_faults(self):
        text = (REPO_ROOT / "workbench" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("create_server(args.data_dir, args.port)", text)
        self.assertNotIn("clock=", text)
        self.assertNotIn("faults=", text)

    def test_fixed_clock_launcher_exists_and_serves_the_fixed_date(self):
        launcher = REPO_ROOT / "tests" / "support" / "serve_fixed_clock.py"
        data_dir = tempfile.TemporaryDirectory()
        self.addCleanup(data_dir.cleanup)
        port = free_port()
        proc = subprocess.Popen([sys.executable, str(launcher), "--data-dir", data_dir.name, "--port", str(port),
                                 "--now", "2030-04-10T10:00:00Z"],
                                cwd=str(REPO_ROOT.parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def shut_down():
            proc.terminate()
            proc.wait(timeout=10)
            proc.stdout.close()
            proc.stderr.close()

        self.addCleanup(shut_down)
        for line in proc.stdout:  # banner
            if line.startswith("Press Ctrl+C"):
                break
        from tests.support.harness import Client
        client = Client(port)
        self.assertEqual(client.get("/api/v1/session").json()["business_date"], "2030-04-10")
        self.assertEqual(client.put_profile().status, 200)


if __name__ == "__main__":
    unittest.main()
