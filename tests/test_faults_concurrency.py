"""T13 atomicity under injected faults, concurrency and storage contention."""

import sqlite3
import threading
import unittest

from tests.support.harness import WorkbenchTestCase, db_file, db_query, new_key


class T13Atomicity(WorkbenchTestCase):
    def setUp(self):
        super().setUp()
        self.running = self.start_with_profile()
        self.client = self.running.client
        self.faults = self.running.faults
        self.lead_id = self.client.create_lead().json()["lead"]["id"]
        self.before = self.counts()

    def first_outreach(self, key):
        return self.client.outreach(self.lead_id, 1, text="Await reply", due="2030-04-11", key=key)

    def assert_nothing_changed(self):
        # a SEPARATE sqlite3 connection, bypassing the application
        self.assertEqual(self.counts(), self.before)
        lead = db_query(self.data_dir, "SELECT version, stage, next_action_text, next_action_due FROM lead WHERE id = ?",
                        (self.lead_id,))
        self.assertEqual(lead, [(1, "new", None, None)])
        self.assertEqual(db_query(self.data_dir, "SELECT COUNT(*) FROM activity")[0][0], 0)

    def test_fault_after_activity_insert_rolls_everything_back(self):
        self.faults.arm("activity.after_insert")
        response = self.first_outreach("fault-after-insert-key-1")
        self.assert_error(response, 500, "INTERNAL_ERROR")
        self.assertIn("activity.after_insert", self.faults.hits)
        self.assertNotIn("injected", response.text)
        self.assert_nothing_changed()

    def test_fault_before_commit_rolls_everything_back(self):
        self.faults.arm("before_commit")
        response = self.first_outreach("fault-before-commit-key-1")
        self.assert_error(response, 500, "INTERNAL_ERROR")
        self.assertNotIn("RuntimeError", response.text)
        self.assert_nothing_changed()
        self.assertEqual(db_query(self.data_dir, "SELECT COUNT(*) FROM idempotency WHERE key LIKE 'fault-%'")[0][0], 0)

    def test_clean_retry_after_fault_succeeds_exactly_once(self):
        key = "retry-after-fault-key-01"
        for point in ("activity.after_insert", "before_commit"):
            self.faults.arm(point)
            self.assert_error(self.first_outreach(key), 500, "INTERNAL_ERROR")
            self.faults.disarm()
        self.assert_nothing_changed()

        retry = self.first_outreach(key)
        self.assertEqual(retry.status, 201)
        self.assertNotIn("idempotent-replayed", retry.headers)
        self.assertEqual(retry.json()["lead"]["version"], 2)
        self.assertEqual(db_query(self.data_dir, "SELECT COUNT(*) FROM activity WHERE lead_id = ?", (self.lead_id,))[0][0], 1)
        audit = db_query(self.data_dir, "SELECT COUNT(*) FROM audit WHERE command_type = 'activity.outreach'")
        self.assertEqual(audit[0][0], 1)
        self.assertEqual(db_query(self.data_dir, "SELECT version FROM lead")[0][0], 2)

        again = self.first_outreach(key)  # now a genuine replay
        self.assertEqual((again.status, again.raw), (201, retry.raw))
        self.assertEqual(again.headers["idempotent-replayed"], "true")
        self.assertEqual(db_query(self.data_dir, "SELECT COUNT(*) FROM activity")[0][0], 1)

    def test_fault_in_every_command_leaves_no_trace(self):
        cases = [
            ("PUT", "/api/v1/profile", {"version": 1, "display_label": "Changed"}),
            ("POST", "/api/v1/leads", {"label": "X", "country_tag": "NL", "work_mode": "remote", "source_channel": "manual"}),
            ("PATCH", f"/api/v1/leads/{self.lead_id}", {"version": 1, "label": "Renamed"}),
            ("PUT", f"/api/v1/leads/{self.lead_id}/next-action",
             {"version": 1, "next_action": {"text": "x", "due_date": "2030-04-12"}}),
            ("POST", f"/api/v1/leads/{self.lead_id}/stage",
             {"version": 1, "stage": "researching", "next_action": {"text": "x", "due_date": "2030-04-12"}}),
        ]
        dump_before = self.snapshot()
        self.faults.arm("before_commit")
        for method, path, body in cases:
            with self.subTest(path=path, method=method):
                self.assert_error(self.client.mutate(method, path, body), 500, "INTERNAL_ERROR")
        self.assertEqual(self.snapshot(), dump_before)


class T13Concurrency(WorkbenchTestCase):
    def test_two_simultaneous_outreaches_one_wins(self):
        running = self.start_with_profile()
        lead_id = running.client.create_lead().json()["lead"]["id"]
        barrier = threading.Barrier(2)
        results = {}

        def worker(name, text):
            client = running.client.__class__(running.port, running.server.session_token)
            barrier.wait()
            results[name] = client.outreach(lead_id, 1, text=text, due="2030-04-11", summary=f"Message {name}")

        threads = [threading.Thread(target=worker, args=("a", "Follow up A")),
                   threading.Thread(target=worker, args=("b", "Follow up B"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        statuses = sorted(response.status for response in results.values())
        self.assertEqual(statuses, [201, 409])
        loser = next(r for r in results.values() if r.status == 409)
        self.assertEqual(loser.json()["error"]["code"], "STALE_VERSION")
        self.assertEqual(loser.json()["error"]["current_version"], 2)
        self.assertEqual(db_query(self.data_dir, "SELECT COUNT(*) FROM activity")[0][0], 1)
        self.assertEqual(db_query(self.data_dir, "SELECT version FROM lead")[0][0], 2)
        winner = next(r for r in results.values() if r.status == 201)
        row = db_query(self.data_dir, "SELECT next_action_text FROM lead")
        self.assertEqual(row, [(winner.json()["lead"]["next_action"]["text"],)])


class T13StorageBusy(WorkbenchTestCase):
    def test_locked_database_gives_503_and_writes_nothing(self):
        running = self.start_with_profile(busy_timeout_ms=200)
        client = running.client
        lead_id = client.create_lead().json()["lead"]["id"]
        before = self.counts()

        blocker = sqlite3.connect(str(db_file(self.data_dir)), isolation_level=None, timeout=1)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            response = client.outreach(lead_id, 1, key=new_key())
            self.assert_error(response, 503, "STORAGE_BUSY")
            self.assertEqual(response.headers["retry-after"], "1")
            create = client.create_lead("Blocked")
            self.assert_error(create, 503, "STORAGE_BUSY")
            # reads still work while a writer holds the lock (WAL)
            self.assertEqual(client.get("/api/v1/dashboard").status, 200)
        finally:
            blocker.execute("ROLLBACK")

        self.assertEqual(self.counts(), before)
        retry = client.outreach(lead_id, 1, key=new_key())
        self.assertEqual(retry.status, 201)


if __name__ == "__main__":
    unittest.main()
