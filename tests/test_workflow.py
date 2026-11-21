"""T03 first outreach, T04 stage/next-action rules, T08 dashboard buckets."""

import unittest

from tests.support.harness import WorkbenchTestCase, db_query


class LeadTestCase(WorkbenchTestCase):
    def setUp(self):
        super().setUp()
        self.running = self.start_with_profile()
        self.client = self.running.client

    def new_lead(self, label="Fictional Co"):
        response = self.client.create_lead(label)
        self.assertEqual(response.status, 201)
        return response.json()["lead"]["id"]

    def rejected_leaves_no_trace(self, action):
        """Run `action` (returns a Response), assert the whole database is unchanged."""
        before = self.snapshot()
        response = action()
        self.assertGreaterEqual(response.status, 400)
        self.assertEqual(self.snapshot(), before)
        return response


class T03FirstOutreach(LeadTestCase):
    def test_first_outreach_contacts_the_lead(self):
        lead_id = self.new_lead()
        response = self.client.outreach(lead_id, 1, text="Review the fictional reply", due="2030-04-11",
                                        business_date="2030-04-10", summary="Sent fictional intro")
        self.assertEqual(response.status, 201, response.raw)
        data = response.json()
        lead = data["lead"]
        self.assertEqual((lead["stage"], lead["version"]), ("contacted", 2))
        self.assertEqual(lead["next_action"], {"text": "Review the fictional reply", "due_date": "2030-04-11"})
        self.assertEqual(lead["bucket"], "upcoming")
        self.assertEqual(data["activity"]["kind"], "outreach")
        self.assertEqual(data["activity"]["business_date"], "2030-04-10")

        detail = self.client.lead(lead_id)
        self.assertEqual(len(detail["activities"]), 1)
        self.assertEqual(detail["activities"][0]["summary"], "Sent fictional intro")
        self.assertEqual(detail["lead"]["next_action"], lead["next_action"])

        audit = db_query(self.data_dir, "SELECT command_type, entity_type, resulting_version, from_stage, to_stage"
                         " FROM audit WHERE entity_type = 'lead' AND entity_id = ? AND command_type LIKE 'activity.%'",
                         (lead_id,))
        self.assertEqual(audit, [("activity.outreach", "lead", 2, "new", "contacted")])
        self.assertEqual(self.counts()["activity"], 1)

        counts = self.client.get("/api/v1/dashboard").json()["counts"]
        self.assertEqual((counts["upcoming"], counts["today"], counts["overdue"], counts["no_action"]), (1, 0, 0, 0))

    def test_audit_holds_no_free_text(self):
        lead_id = self.new_lead("Secret Label Text")
        self.client.outreach(lead_id, 1, text="Secret action", summary="Secret summary")
        dump = "\n".join(str(row) for row in db_query(self.data_dir, "SELECT * FROM audit"))
        for secret in ("Secret", "key-"):
            self.assertNotIn(secret, dump)


class T04Rules(LeadTestCase):
    def test_new_to_contacted_via_outreach(self):
        lead_id = self.new_lead()
        response = self.client.outreach(lead_id, 1)
        self.assertEqual(response.json()["lead"]["stage"], "contacted")

    def test_new_to_researching_to_contacted(self):
        lead_id = self.new_lead()
        response = self.client.mutate("POST", f"/api/v1/leads/{lead_id}/stage", {
            "version": 1, "stage": "researching", "next_action": {"text": "Read the fictional brief", "due_date": "2030-04-12"}})
        self.assertEqual(response.status, 200, response.raw)
        lead = response.json()["lead"]
        self.assertEqual((lead["stage"], lead["version"]), ("researching", 2))
        self.assertEqual(lead["next_action"]["text"], "Read the fictional brief")
        audit = db_query(self.data_dir, "SELECT command_type, from_stage, to_stage, resulting_version FROM audit"
                         " WHERE command_type = 'lead.stage.change'")
        self.assertEqual(audit, [("lead.stage.change", "new", "researching", 2)])

        response = self.client.outreach(lead_id, 2, text="Await the fictional reply", due="2030-04-15")
        lead = response.json()["lead"]
        self.assertEqual((lead["stage"], lead["version"]), ("contacted", 3))
        self.assertEqual(lead["next_action"]["text"], "Await the fictional reply")

    def test_researching_may_reuse_existing_action(self):
        lead_id = self.new_lead()
        self.client.put_action(lead_id, 1, "Existing action", "2030-04-20")
        response = self.client.mutate("POST", f"/api/v1/leads/{lead_id}/stage", {"version": 2, "stage": "researching"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json()["lead"]["next_action"]["text"], "Existing action")

    def test_contacted_cannot_be_set_directly(self):
        lead_id = self.new_lead()
        response = self.rejected_leaves_no_trace(lambda: self.client.mutate(
            "POST", f"/api/v1/leads/{lead_id}/stage", {"version": 1, "stage": "contacted",
                                                       "next_action": {"text": "x", "due_date": "2030-04-12"}}))
        self.assert_error(response, 422, "INVALID_TRANSITION")

    def test_other_transitions_rejected(self):
        lead_id = self.new_lead()
        self.client.mutate("POST", f"/api/v1/leads/{lead_id}/stage", {
            "version": 1, "stage": "researching", "next_action": {"text": "x", "due_date": "2030-04-12"}})
        for stage in ("new", "researching", "contacted"):
            with self.subTest(stage):
                response = self.rejected_leaves_no_trace(lambda: self.client.mutate(
                    "POST", f"/api/v1/leads/{lead_id}/stage", {"version": 2, "stage": stage}))
                self.assert_error(response, 422, "INVALID_TRANSITION")

    def test_unknown_stage_value_is_validation_error(self):
        lead_id = self.new_lead()
        response = self.rejected_leaves_no_trace(lambda: self.client.mutate(
            "POST", f"/api/v1/leads/{lead_id}/stage", {"version": 1, "stage": "won"}))
        self.assert_error(response, 422, "VALIDATION_FAILED")

    def test_first_outreach_without_next_action_rejected(self):
        lead_id = self.new_lead()
        response = self.rejected_leaves_no_trace(lambda: self.client.activity(lead_id, 1, "outreach"))
        self.assert_error(response, 422, "NEXT_ACTION_REQUIRED")
        self.assertIn("next_action", response.json()["error"]["fields"])

    def test_researching_without_any_action_rejected(self):
        lead_id = self.new_lead()
        response = self.rejected_leaves_no_trace(lambda: self.client.mutate(
            "POST", f"/api/v1/leads/{lead_id}/stage", {"version": 1, "stage": "researching"}))
        self.assert_error(response, 422, "NEXT_ACTION_REQUIRED")

    def test_cannot_clear_action_on_researching_or_contacted(self):
        researching = self.new_lead("Researching")
        self.client.mutate("POST", f"/api/v1/leads/{researching}/stage", {
            "version": 1, "stage": "researching", "next_action": {"text": "x", "due_date": "2030-04-12"}})
        contacted = self.new_lead("Contacted")
        self.client.outreach(contacted, 1)
        for lead_id in (researching, contacted):
            with self.subTest(lead_id=lead_id):
                response = self.rejected_leaves_no_trace(lambda: self.client.mutate(
                    "PUT", f"/api/v1/leads/{lead_id}/next-action", {"version": 2, "next_action": None}))
                self.assert_error(response, 422, "NEXT_ACTION_REQUIRED")
                self.assertIsNotNone(self.client.lead(lead_id)["lead"]["next_action"])

    def test_new_lead_may_clear_its_action(self):
        lead_id = self.new_lead()
        self.client.put_action(lead_id, 1, "Temporary", "2030-04-20")
        response = self.client.mutate("PUT", f"/api/v1/leads/{lead_id}/next-action", {"version": 2, "next_action": None})
        self.assertEqual(response.status, 200)
        lead = response.json()["lead"]
        self.assertEqual((lead["next_action"], lead["bucket"], lead["version"]), (None, "no_action", 3))

    def test_activity_cannot_clear_action(self):
        lead_id = self.new_lead()
        self.client.outreach(lead_id, 1)
        response = self.rejected_leaves_no_trace(lambda: self.client.mutate(
            "POST", f"/api/v1/leads/{lead_id}/activities",
            {"version": 2, "kind": "note", "business_date": "2030-04-10", "summary": "x", "next_action": None}))
        self.assert_error(response, 422, "VALIDATION_FAILED")

    def test_future_activity_date_rejected(self):
        lead_id = self.new_lead()
        response = self.rejected_leaves_no_trace(
            lambda: self.client.outreach(lead_id, 1, business_date="2030-04-11"))
        self.assert_error(response, 422, "VALIDATION_FAILED")
        self.assertIn("business_date", response.json()["error"]["fields"])

    def test_activity_dated_today_and_past_accepted(self):
        lead_id = self.new_lead()
        self.assertEqual(self.client.activity(lead_id, 1, "note", "2030-04-10").status, 201)
        self.assertEqual(self.client.activity(lead_id, 2, "note", "2030-01-01").status, 201)
        self.assertEqual(self.client.activity(lead_id, 3, "note", "2000-01-01").status, 201)

    def test_invalid_dates_rejected(self):
        lead_id = self.new_lead()
        for bad in ("2030-02-30", "2030-4-1", "1999-12-31", "2100-01-01", "2030-02-29", "tomorrow", "", None):
            with self.subTest(bad=bad):
                response = self.rejected_leaves_no_trace(
                    lambda: self.client.outreach(lead_id, 1, business_date=bad))
                self.assert_error(response, 422, "VALIDATION_FAILED")
        for bad_due in ("2030-02-30", "2030-4-1", "1999-12-31", "2100-01-01"):
            with self.subTest(due=bad_due):
                response = self.rejected_leaves_no_trace(lambda: self.client.outreach(lead_id, 1, due=bad_due))
                self.assert_error(response, 422, "VALIDATION_FAILED")
                response = self.rejected_leaves_no_trace(lambda: self.client.put_action(lead_id, 1, "x", bad_due))
                self.assert_error(response, 422, "VALIDATION_FAILED")

    def test_leap_day_activity_accepted_when_clock_allows(self):
        self.running.clock.set("2028-03-01T10:00:00Z")
        lead_id = self.new_lead()
        self.assertEqual(self.client.activity(lead_id, 1, "note", "2028-02-29").status, 201)

    def test_past_due_date_is_allowed(self):
        lead_id = self.new_lead()
        response = self.client.put_action(lead_id, 1, "Late already", "2030-01-01")
        self.assertEqual(response.json()["lead"]["bucket"], "overdue")

    def test_later_note_keeps_stage_and_action_and_bumps_version_once(self):
        lead_id = self.new_lead()
        self.client.outreach(lead_id, 1, text="Await reply", due="2030-04-15")
        response = self.client.activity(lead_id, 2, "note", "2030-04-10", "Internal fictional note")
        lead = response.json()["lead"]
        self.assertEqual((lead["stage"], lead["version"]), ("contacted", 3))
        self.assertEqual(lead["next_action"], {"text": "Await reply", "due_date": "2030-04-15"})
        audit = db_query(self.data_dir, "SELECT command_type, from_stage, to_stage FROM audit"
                         " WHERE command_type = 'activity.note'")
        self.assertEqual(audit, [("activity.note", None, None)])

    def test_note_with_next_action_replaces_it(self):
        lead_id = self.new_lead()
        self.client.outreach(lead_id, 1, text="Old", due="2030-04-15")
        response = self.client.activity(lead_id, 2, "note", next_action={"text": "New", "due_date": "2030-05-01"})
        self.assertEqual(response.json()["lead"]["next_action"], {"text": "New", "due_date": "2030-05-01"})

    def test_second_outreach_on_contacted_lead(self):
        lead_id = self.new_lead()
        self.client.outreach(lead_id, 1, text="First follow-up", due="2030-04-15")
        response = self.client.activity(lead_id, 2, "outreach", summary="Second message")  # action optional now
        self.assertEqual(response.status, 201)
        lead = response.json()["lead"]
        self.assertEqual((lead["stage"], lead["version"], lead["next_action"]["text"]), ("contacted", 3, "First follow-up"))
        audit = db_query(self.data_dir, "SELECT from_stage, to_stage FROM audit WHERE command_type = 'activity.outreach'"
                         " ORDER BY id")
        self.assertEqual(audit, [("new", "contacted"), (None, None)])

    def test_replacing_follow_up_leaves_exactly_one_current_action(self):
        lead_id = self.new_lead()
        self.client.outreach(lead_id, 1, text="First", due="2030-04-15")
        response = self.client.put_action(lead_id, 2, "Replacement", "2030-04-20")
        lead = response.json()["lead"]
        self.assertEqual(lead["next_action"], {"text": "Replacement", "due_date": "2030-04-20"})
        self.assertEqual(lead["version"], 3)
        rows = db_query(self.data_dir, "SELECT next_action_text, next_action_due FROM lead")
        self.assertEqual(rows, [("Replacement", "2030-04-20")])
        self.assertEqual(self.client.get("/api/v1/dashboard").json()["counts"]["total"], 1)

    def test_previous_activities_are_preserved_in_order(self):
        lead_id = self.new_lead()
        self.client.outreach(lead_id, 1, summary="one")
        self.client.activity(lead_id, 2, "note", summary="two")
        self.client.put_action(lead_id, 3, "Changed", "2030-06-01")
        self.client.activity(lead_id, 4, "note", summary="three")
        activities = self.client.lead(lead_id)["activities"]
        self.assertEqual([a["summary"] for a in activities], ["one", "two", "three"])
        self.assertEqual(activities, sorted(activities, key=lambda a: a["id"]))

    def test_every_rejected_write_adds_no_rows(self):
        lead_id = self.new_lead()
        self.client.outreach(lead_id, 1)
        before = self.counts()
        rejected = [
            self.client.mutate("PUT", f"/api/v1/leads/{lead_id}/next-action", {"version": 2, "next_action": None}),
            self.client.activity(lead_id, 1, "note"),  # stale
            self.client.activity(lead_id, 2, "bogus"),
            self.client.mutate("POST", f"/api/v1/leads/{lead_id}/stage", {"version": 2, "stage": "contacted"}),
            self.client.mutate("POST", "/api/v1/leads/9999/activities",
                               {"version": 1, "kind": "note", "business_date": "2030-04-10", "summary": "x"}),
        ]
        self.assertEqual([r.status for r in rejected], [422, 409, 422, 422, 404])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.client.lead(lead_id)["lead"]["version"], 2)

    def test_replay_stored_only_for_successes(self):
        lead_id = self.new_lead()
        key = "failed-then-fixed-0123456789"
        bad = self.client.outreach(lead_id, 1, due="2100-01-01", key=key)
        self.assertEqual(bad.status, 422)
        good = self.client.outreach(lead_id, 1, key=key)  # same key, different (valid) body
        self.assertEqual(good.status, 201)


class T08Dashboard(LeadTestCase):
    def lead_with_due(self, label, due):
        lead_id = self.new_lead(label)
        if due is not None:
            self.assertEqual(self.client.put_action(lead_id, 1, f"Action for {label}", due).status, 200)
        return lead_id

    def test_exactly_one_per_bucket_and_counts_match_lists(self):
        overdue = self.lead_with_due("Due yesterday", "2030-04-09")
        today = self.lead_with_due("Due today", "2030-04-10")
        upcoming = self.lead_with_due("Due tomorrow", "2030-04-11")
        none = self.lead_with_due("No action", None)
        dashboard = self.client.get("/api/v1/dashboard").json()
        self.assertEqual(dashboard["business_date"], "2030-04-10")
        self.assertEqual(dashboard["counts"], {"overdue": 1, "today": 1, "upcoming": 1, "no_action": 1, "total": 4})
        expected = {"overdue": overdue, "today": today, "upcoming": upcoming, "no_action": none}
        for bucket, lead_id in expected.items():
            with self.subTest(bucket):
                self.assertEqual([row["id"] for row in dashboard["groups"][bucket]], [lead_id])
                listing = self.client.get(f"/api/v1/leads?bucket={bucket}").json()
                self.assertEqual(listing["total"], dashboard["counts"][bucket])
                self.assertEqual([lead["id"] for lead in listing["leads"]], [lead_id])
                row = dashboard["groups"][bucket][0]
                detail = self.client.lead(lead_id)["lead"]
                self.assertEqual(row["next_action"], detail["next_action"])
                self.assertEqual(row["bucket"], bucket)
                self.assertEqual(detail["bucket"], bucket)
                self.assertEqual(row["label"], detail["label"])
                self.assertEqual(row["stage"], detail["stage"])

    def test_sort_order_due_date_then_label_then_id(self):
        zulu = self.lead_with_due("Zulu", "2030-04-08")
        beta = self.lead_with_due("beta", "2030-04-09")
        alpha_a = self.lead_with_due("Alpha", "2030-04-09")
        alpha_b = self.lead_with_due("Alpha", "2030-04-09")
        gamma = self.lead_with_due("Gamma", "2030-04-02")
        dashboard = self.client.get("/api/v1/dashboard").json()
        self.assertEqual([row["id"] for row in dashboard["groups"]["overdue"]],
                         [gamma, zulu, alpha_a, alpha_b, beta])

    def test_no_action_sorted_by_label_then_id(self):
        b2 = self.lead_with_due("b", None)
        a = self.lead_with_due("A", None)
        b1 = self.lead_with_due("B", None)
        dashboard = self.client.get("/api/v1/dashboard").json()
        self.assertEqual([row["id"] for row in dashboard["groups"]["no_action"]], [a, b2, b1])

    def test_bucket_filter_combines_with_other_filters(self):
        self.lead_with_due("Due today NL", "2030-04-10")
        other = self.client.create_lead("Due today abroad", country_tag="other").json()["lead"]["id"]
        self.client.put_action(other, 1, "x", "2030-04-10")
        listing = self.client.get("/api/v1/leads?bucket=today&country=other").json()
        self.assertEqual([lead["id"] for lead in listing["leads"]], [other])


if __name__ == "__main__":
    unittest.main()
