"""Regression tests for findings from the independent review, plus exact-spec fixtures."""

import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path

from tests.support.harness import REPO_ROOT, WorkbenchTestCase, db_query, free_port
from tests.test_process import ProcessTestCase, run_cli


class T02ExactFixture(WorkbenchTestCase):
    """The four-lead example: NL matches two, remote matches two, intersection one."""

    def test_four_lead_example(self):
        client = self.start_with_profile().client
        for country, mode in (("NL", "remote"), ("NL", "onsite"), ("other", "remote"), ("other", "onsite")):
            self.assertEqual(client.create_lead(f"Fictional {country} {mode}", country, mode).status, 201)

        def total(query):
            return client.get("/api/v1/leads" + query).json()["total"]

        self.assertEqual(total("?country=NL"), 2)
        self.assertEqual(total("?mode=remote"), 2)
        self.assertEqual(total("?country=NL&mode=remote"), 1)
        self.assertEqual(client.create_lead("Fictional unknown remote", "unknown", "remote").status, 201)
        self.assertEqual(total("?country=NL"), 2, "unknown does not count as NL")


class ControlCharacters(WorkbenchTestCase):
    """A NUL byte used to slip past validation and fail a CHECK constraint (500)."""

    def test_control_characters_are_validation_errors(self):
        client = self.start_with_profile().client
        lead = client.create_lead("Fictional lead").json()["lead"]
        before = self.snapshot()
        cases = [
            ("display_label", lambda v: client.put_profile(v, version=1)),
            ("label", lambda v: client.create_lead(v)),
            ("source_label", lambda v: client.create_lead("ok", source_label=v)),
            ("summary", lambda v: client.activity(lead["id"], 1, summary=v)),
            ("next_action.text", lambda v: client.put_action(lead["id"], 1, v, "2030-04-11")),
        ]
        for field, send in cases:
            for value in ("\u0000abc", "a\u0007b", "x\u007f"):
                with self.subTest(field=field, value=repr(value)):
                    response = send(value)
                    self.assert_error(response, 422, "VALIDATION_FAILED")
                    self.assertIn(field, response.json()["error"]["fields"])
        self.assertEqual(self.snapshot(), before)

    def test_newlines_and_tabs_are_still_allowed_in_a_summary(self):
        client = self.start_with_profile().client
        lead = client.create_lead("Fictional lead").json()["lead"]
        response = client.activity(lead["id"], 1, summary="Line one\n\tLine two")
        self.assertEqual(response.status, 201)


class RejectedWalDatabase(ProcessTestCase):
    """Rejecting a WAL-mode file must not leave -wal/-shm files behind."""

    def make_wal_db(self, path, application_id, user_version):
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE unrelated (x)")
        conn.execute(f"PRAGMA application_id = {application_id}")
        conn.execute(f"PRAGMA user_version = {user_version}")
        conn.commit()
        conn.close()

    def check(self, application_id, user_version):
        data_dir = self.root / f"wal-{application_id}-{user_version}"
        data_dir.mkdir()
        path = data_dir / "workbench.sqlite3"
        self.make_wal_db(path, application_id, user_version)
        self.assertEqual([p.name for p in data_dir.iterdir()], ["workbench.sqlite3"])
        before = path.read_bytes()
        result = run_cli("--data-dir", str(data_dir), "--port", str(free_port()))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([p.name for p in data_dir.iterdir()], ["workbench.sqlite3"], "no sidecar files")

    def test_foreign_wal_database(self):
        self.check(0, 0)

    def test_our_id_with_unknown_schema_version(self):
        self.check(1145459522, 2)


class CliEdgeCases(ProcessTestCase):
    def test_non_numeric_port_message(self):
        result = run_cli("--data-dir", str(self.root / "d"), "--port", "abc")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--port: not a number", result.stderr)
        self.assertFalse((self.root / "d").exists())

    def test_database_path_that_is_a_directory(self):
        data_dir = self.root / "d"
        (data_dir / "workbench.sqlite3").mkdir(parents=True)
        result = run_cli("--data-dir", str(data_dir), "--port", str(free_port()))
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not open the database", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


@unittest.skipUnless(shutil.which("git") and (REPO_ROOT / ".git").exists(), "needs git and a checkout")
class T15GitIgnore(unittest.TestCase):
    """Databases, sidecars, logs, runtime files, test data, profiles, caches, envs and secrets stay out of Git."""

    IGNORED = [
        "data/main/workbench.sqlite3", "anywhere/workbench.sqlite3", "x.sqlite3-wal", "x.sqlite3-shm",
        "x.db", "x.db-journal", "logs/server.log", "server.log", "run/server.pid", "tmp/x",
        "test-data/a", "demo-data/a", ".browser-profile/Default/Cookies", "playwright-report/index.html",
        "test-results/a.png", "screenshots/a.png", "__pycache__/a.pyc", "workbench/__pycache__/x.cpython-312.pyc",
        ".pytest_cache/v", ".venv/bin/python", "venv/x", "node_modules/x/index.js", ".env", ".env.local",
        "secrets/token.txt", "credentials.json", "key.pem",
    ]
    TRACKED = ["workbench/server.py", "workbench/static/js/app.js", "tests/test_dates.py", "README.md", "LICENSE",
               ".gitignore", "tests/browser/acceptance.mjs"]

    def check_ignore(self, path):
        return subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=str(REPO_ROOT)).returncode == 0

    def test_runtime_and_private_files_are_ignored(self):
        for path in self.IGNORED:
            with self.subTest(path=path):
                self.assertTrue(self.check_ignore(path), path)

    def test_source_files_are_not_ignored(self):
        for path in self.TRACKED:
            with self.subTest(path=path):
                self.assertFalse(self.check_ignore(path), path)


if __name__ == "__main__":
    unittest.main()
