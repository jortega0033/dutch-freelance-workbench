"""T10 lost responses, replay, key reuse and two-tab conflicts."""

import unittest

from tests.support.harness import WorkbenchTestCase, db_query


class T10LostResponse(WorkbenchTestCase):
    def setUp(self):
        super().setUp()
        self.running = self.start_with_profile()
        self.client = self.running.client
        self.lead_id = self.client.create_lead().json()["lead"]["id"]

    def outreach_body(self, version=1):
        return {"version": version, "kind": "outreach", "business_date": "2030-04-10",
                "summary": "Sent a fictional message", "next_action": {"text": "Await reply", "due_date": "2030-04-11"}}

    def post_outreach(self, client=None, key="lost-response-key-0001", body=None):
        return (client or self.client).mutate("POST", f"/api/v1/leads/{self.lead_id}/activities",
                                              body or self.outreach_body(), key=key)

    def rows_for_lead(self):
        return {
            "activities": db_query(self.data_dir, "SELECT COUNT(*) FROM activity WHERE lead_id = ?", (self.lead_id,))[0][0],
            "audit": db_query(self.data_dir, "SELECT COUNT(*) FROM audit WHERE entity_type = 'lead' AND entity_id = ?"
                              " AND command_type = 'activity.outreach'", (self.lead_id,))[0][0],
            "version": db_query(self.data_dir, "SELECT version FROM lead WHERE id = ?", (self.lead_id,))[0][0],
        }

    def test_retry_returns_identical_response_without_new_rows(self):
        first = self.post_outreach()  # pretend this response was lost
        self.assertEqual(first.status, 201)
        before = self.rows_for_lead()
        self.assertEqual(before, {"activities": 1, "audit": 1, "version": 2})
        counts = self.counts()

        retry = self.post_outreach()
        self.assertEqual((retry.status, retry.raw), (first.status, first.raw))
        self.assertEqual(retry.headers["idempotent-replayed"], "true")
        self.assertNotIn("idempotent-replayed", first.headers)
        self.assertEqual(self.rows_for_lead(), before)
        self.assertEqual(self.counts(), counts)

    def test_replay_survives_a_newer_change(self):
        first = self.post_outreach()
        newer = self.client.put_action(self.lead_id, 2, "Newer action", "2030-05-01")
        self.assertEqual(newer.status, 200)
        counts = self.counts()

        retry = self.post_outreach()  # body still says version 1, which is now stale
        self.assertEqual((retry.status, retry.raw), (first.status, first.raw))
        self.assertEqual(retry.headers["idempotent-replayed"], "true")
        self.assertEqual(self.counts(), counts)
        row = db_query(self.data_dir, "SELECT version, next_action_text, next_action_due FROM lead WHERE id = ?",
                       (self.lead_id,))
        self.assertEqual(row, [(3, "Newer action", "2030-05-01")])

    def test_replay_survives_restart_and_new_session_token(self):
        first = self.post_outreach()
        old_token = self.running.server.session_token
        self.running.stop()
        restarted = self.start()
        self.assertNotEqual(restarted.server.session_token, old_token)
        counts = self.counts()
        retry = self.post_outreach(client=restarted.client)
        self.assertEqual((retry.status, retry.raw), (first.status, first.raw))
        self.assertEqual(retry.headers["idempotent-replayed"], "true")
        self.assertEqual(self.counts(), counts)

    def test_next_action_put_replay(self):
        body = {"version": 1, "next_action": {"text": "Call", "due_date": "2030-04-12"}}
        path = f"/api/v1/leads/{self.lead_id}/next-action"
        first = self.client.mutate("PUT", path, body, key="put-replay-key-00000001")
        retry = self.client.mutate("PUT", path, body, key="put-replay-key-00000001")
        self.assertEqual((retry.status, retry.raw), (first.status, first.raw))
        self.assertEqual(retry.headers["idempotent-replayed"], "true")
        self.assertEqual(self.client.lead(self.lead_id)["lead"]["version"], 2)

    def test_same_key_with_changed_payload_is_key_reused(self):
        self.post_outreach()
        counts = self.counts()
        changed = self.outreach_body()
        changed["summary"] = "A different summary"
        response = self.post_outreach(body=changed)
        self.assert_error(response, 409, "KEY_REUSED")
        self.assertNotIn("idempotent-replayed", response.headers)
        self.assertEqual(self.counts(), counts)

    def test_same_key_on_different_target_is_key_reused(self):
        body = {"version": 1, "next_action": {"text": "Call", "due_date": "2030-04-12"}}
        self.client.mutate("PUT", f"/api/v1/leads/{self.lead_id}/next-action", body, key="shared-key-000000000001")
        other = self.client.create_lead("Another").json()["lead"]["id"]
        response = self.client.mutate("PUT", f"/api/v1/leads/{other}/next-action", body, key="shared-key-000000000001")
        self.assert_error(response, 409, "KEY_REUSED")

    def test_key_order_in_body_does_not_matter(self):
        body = self.outreach_body()
        reordered = {key: body[key] for key in reversed(list(body))}
        first = self.post_outreach(body=body)
        retry = self.post_outreach(body=reordered)
        self.assertEqual(retry.raw, first.raw)
        self.assertEqual(retry.headers["idempotent-replayed"], "true")

    def test_two_tabs_stale_version_conflict(self):
        tab_a = self.client
        tab_b = self.start().client  # same database, independent client
        a_view = tab_a.lead(self.lead_id)["lead"]
        b_view = tab_b.lead(self.lead_id)["lead"]
        self.assertEqual(a_view["version"], b_view["version"])
        n = a_view["version"]

        saved = tab_a.put_action(self.lead_id, n, "Tab A follow-up", "2030-04-20")
        self.assertEqual(saved.status, 200)

        conflict = tab_b.put_action(self.lead_id, n, "Tab B follow-up", "2030-04-25")
        self.assert_error(conflict, 409, "STALE_VERSION")
        error = conflict.json()["error"]
        self.assertEqual(error["current_version"], n + 1)
        self.assertEqual(error["reload"], f"/api/v1/leads/{self.lead_id}")
        self.assertEqual(tab_b.get(error["reload"]).json()["lead"]["next_action"]["text"], "Tab A follow-up")

        row = db_query(self.data_dir, "SELECT version, next_action_text FROM lead WHERE id = ?", (self.lead_id,))
        self.assertEqual(row, [(n + 1, "Tab A follow-up")])

        # after reviewing, tab B can save with the new version and a new key
        self.assertEqual(tab_b.put_action(self.lead_id, n + 1, "Tab B follow-up", "2030-04-25").status, 200)

    def test_stale_conflict_is_not_cached_as_a_replay(self):
        self.client.put_action(self.lead_id, 1, "First", "2030-04-12")
        key = "stale-then-fixed-0000001"
        stale = self.client.mutate("PUT", f"/api/v1/leads/{self.lead_id}/next-action",
                                   {"version": 1, "next_action": {"text": "Second", "due_date": "2030-04-13"}}, key=key)
        self.assert_error(stale, 409, "STALE_VERSION")
        self.assertEqual(db_query(self.data_dir, "SELECT COUNT(*) FROM idempotency WHERE key = ?", (key,))[0][0], 0)


if __name__ == "__main__":
    unittest.main()
