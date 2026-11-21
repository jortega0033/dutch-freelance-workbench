"""T01 settings and T02 leads."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support.harness import WorkbenchTestCase, db_query, table_counts


class T01Settings(WorkbenchTestCase):
    def test_empty_directory_first_use(self):
        client = self.start().client
        session = client.get("/api/v1/session").json()
        self.assertFalse(session["has_profile"])
        self.assertEqual(session["business_date"], "2030-04-10")
        self.assertEqual(session["timezone"], "Europe/Amsterdam")
        self.assertEqual(client.get("/api/v1/profile").json(), {"profile": None})
        dashboard = client.get("/api/v1/dashboard").json()
        self.assertFalse(dashboard["has_profile"])
        self.assertEqual(dashboard["counts"], {"overdue": 0, "today": 0, "upcoming": 0, "no_action": 0, "total": 0})
        self.assertEqual(self.counts()["profile"], 0)
        self.assertEqual(self.counts()["lead"], 0)

    def test_invalid_labels_are_rejected_and_nothing_is_saved(self):
        client = self.start().client
        for label in ("", "   ", "\t \n", "x" * 121, " " + "x" * 121, None, 5, ["a"]):
            with self.subTest(label=label):
                response = client.put_profile(label)
                self.assert_error(response, 422, "VALIDATION_FAILED")
                self.assertIn("display_label", response.json()["error"]["fields"])
        self.assertEqual(self.counts(), {"profile": 0, "lead": 0, "activity": 0, "audit": 0, "idempotency": 0})

    def test_label_boundaries_accepted(self):
        client = self.start().client
        self.assertEqual(client.put_profile("x" * 120).status, 200)
        self.assertEqual(client.put_profile("  trimmed  ", version=1).json()["profile"]["display_label"], "trimmed")

    def test_valid_save_creates_exactly_one_profile_and_no_leads(self):
        client = self.start().client
        response = client.put_profile("  Fictional Freelancer  ")
        self.assertEqual(response.status, 200)
        profile = response.json()["profile"]
        self.assertEqual(profile, {"display_label": "Fictional Freelancer", "version": 1,
                                   "timezone": "Europe/Amsterdam", "language": "en"})
        counts = self.counts()
        self.assertEqual((counts["profile"], counts["lead"]), (1, 0))
        self.assertEqual(counts["audit"], 1)
        row = db_query(self.data_dir, "SELECT command_type, entity_type, entity_id, resulting_version FROM audit")
        self.assertEqual(row, [("profile.save", "profile", 1, 1)])
        self.assertTrue(client.get("/api/v1/session").json()["has_profile"])

    def test_stale_version_zero_conflicts(self):
        client = self.start().client
        self.assertEqual(client.put_profile("First").status, 200)
        response = client.put_profile("Second", version=0)
        self.assert_error(response, 409, "STALE_VERSION")
        error = response.json()["error"]
        self.assertEqual(error["current_version"], 1)
        self.assertEqual(error["reload"], "/api/v1/profile")
        self.assertEqual(client.get("/api/v1/profile").json()["profile"]["display_label"], "First")

    def test_update_increments_version(self):
        client = self.start().client
        client.put_profile("First")
        response = client.put_profile("Second", version=1)
        self.assertEqual(response.json()["profile"]["version"], 2)
        self.assertEqual(self.counts()["profile"], 1)

    def test_profile_survives_restart(self):
        first = self.start()
        first.client.put_profile("Persistent label")
        first.stop()
        second = self.start()
        self.assertNotEqual(first.server.session_token, second.server.session_token)
        profile = second.client.get("/api/v1/profile").json()["profile"]
        self.assertEqual(profile["display_label"], "Persistent label")
        self.assertEqual(profile["version"], 1)
        self.assertTrue(second.client.get("/api/v1/session").json()["has_profile"])

    def test_different_directory_is_independent(self):
        first = self.start()
        first.client.put_profile("Only here")
        with tempfile.TemporaryDirectory() as other:
            second = self.start(data_dir=Path(other) / "other-data")
            self.assertEqual(second.client.get("/api/v1/profile").json(), {"profile": None})
            self.assertFalse(second.client.get("/api/v1/session").json()["has_profile"])
            self.assertEqual(table_counts(Path(other) / "other-data")["profile"], 0)
            second.stop()
        self.assertEqual(first.client.get("/api/v1/profile").json()["profile"]["display_label"], "Only here")


class T02Leads(WorkbenchTestCase):
    def setUp(self):
        super().setUp()
        self.client = self.start_with_profile().client

    def make(self, label, country, mode, **kw):
        response = self.client.create_lead(label, country, mode, **kw)
        self.assertEqual(response.status, 201, response.raw)
        return response.json()["lead"]

    def make_fixture(self):
        return {
            "nl_remote": self.make("Fictional NL Remote", "NL", "remote", source_label="Alpha Board"),
            "nl_onsite": self.make("Fictional NL Onsite", "NL", "onsite", source_label="beta board"),
            "other_remote": self.make("Fictional Other Remote", "other", "remote"),
            "other_onsite": self.make("Fictional Other Onsite", "other", "onsite"),
            "unknown_remote": self.make("Fictional Unknown Remote", "unknown", "remote"),
        }

    def ids(self, query):
        data = self.client.get("/api/v1/leads" + query).json()
        self.assertEqual(data["total"], len(data["leads"]))
        return [lead["id"] for lead in data["leads"]]

    def test_create_defaults(self):
        lead = self.make("Fictional Co", "NL", "remote")
        self.assertEqual((lead["stage"], lead["version"], lead["next_action"], lead["bucket"]),
                         ("new", 1, None, "no_action"))
        self.assertIsNone(lead["source_label"])
        row = db_query(self.data_dir, "SELECT command_type, resulting_version, from_stage, to_stage FROM audit"
                       " WHERE command_type = 'lead.create'")
        self.assertEqual(row, [("lead.create", 1, None, "new")])

    def test_filters_with_precise_counts(self):
        f = self.make_fixture()
        self.assertEqual(self.ids(""), [f[k]["id"] for k in f])
        nl = self.ids("?country=NL")
        self.assertEqual(nl, [f["nl_remote"]["id"], f["nl_onsite"]["id"]])
        remote = self.ids("?mode=remote")
        self.assertEqual(len(remote), 3)
        self.assertEqual(remote, [f["nl_remote"]["id"], f["other_remote"]["id"], f["unknown_remote"]["id"]])
        self.assertEqual(self.ids("?country=NL&mode=remote"), [f["nl_remote"]["id"]])
        self.assertNotIn(f["unknown_remote"]["id"], nl)
        self.assertEqual(self.ids("?country=unknown"), [f["unknown_remote"]["id"]])
        self.assertEqual(self.ids("?country=other&mode=onsite"), [f["other_onsite"]["id"]])
        self.assertEqual(self.ids("?stage=new"), [f[k]["id"] for k in f])
        self.assertEqual(self.ids("?stage=contacted"), [])
        self.assertEqual(self.ids("?source=manual"), [f[k]["id"] for k in f])
        self.assertEqual(self.ids("?source=direct"), [])
        self.assertEqual(self.ids("?country=&mode=&q="), [f[k]["id"] for k in f])

    def test_search_is_case_insensitive_over_label_and_source_label(self):
        f = self.make_fixture()
        self.assertEqual(self.ids("?q=fictional%20nl"), [f["nl_remote"]["id"], f["nl_onsite"]["id"]])
        self.assertEqual(self.ids("?q=UNKNOWN"), [f["unknown_remote"]["id"]])
        self.assertEqual(self.ids("?q=ALPHA%20BOARD"), [f["nl_remote"]["id"]])
        self.assertEqual(self.ids("?q=BETA"), [f["nl_onsite"]["id"]])
        self.assertEqual(self.ids("?q=board"), [f["nl_remote"]["id"], f["nl_onsite"]["id"]])
        self.assertEqual(self.ids("?q=no-such-text"), [])
        self.assertEqual(self.ids("?q=board&country=NL&mode=onsite"), [f["nl_onsite"]["id"]])

    def test_search_treats_wildcards_literally_and_casefolds(self):
        special = self.make("100% Fictional_Co", "NL", "remote")
        german = self.make("Straße Studio", "NL", "remote")
        self.assertEqual(self.ids("?q=100%25"), [special["id"]])
        self.assertEqual(self.ids("?q=%25"), [special["id"]])
        self.assertEqual(self.ids("?q=_c"), [special["id"]])
        self.assertEqual(self.ids("?q=STRASSE"), [german["id"]])

    def test_duplicate_labels_are_distinct_leads(self):
        first = self.make("Same Label", "NL", "remote")
        second = self.make("Same Label", "NL", "remote")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.counts()["lead"], 2)

    def test_same_key_and_body_returns_same_lead(self):
        body = {"label": "Once Only", "country_tag": "NL", "work_mode": "remote", "source_channel": "manual"}
        first = self.client.mutate("POST", "/api/v1/leads", body, key="retry-key-0123456789")
        again = self.client.mutate("POST", "/api/v1/leads", body, key="retry-key-0123456789")
        self.assertEqual((first.status, again.status), (201, 201))
        self.assertEqual(first.raw, again.raw)
        self.assertEqual(again.headers.get("idempotent-replayed"), "true")
        self.assertNotIn("idempotent-replayed", first.headers)
        self.assertEqual(self.counts()["lead"], 1)

    def test_invalid_creates_leave_count_unchanged(self):
        good = {"label": "Fictional", "country_tag": "NL", "work_mode": "remote", "source_channel": "manual"}
        bad_bodies = {
            "bad country": {**good, "country_tag": "Netherlands"},
            "bad mode": {**good, "work_mode": "home"},
            "bad channel": {**good, "source_channel": "fax"},
            "empty label": {**good, "label": "  "},
            "long label": {**good, "label": "x" * 121},
            "long source label": {**good, "source_label": "y" * 121},
            "unexpected field": {**good, "stage": "contacted"},
            "unexpected id": {**good, "id": 5},
            "missing label": {k: v for k, v in good.items() if k != "label"},
            "missing mode": {k: v for k, v in good.items() if k != "work_mode"},
            "label not text": {**good, "label": 7},
        }
        for name, body in bad_bodies.items():
            with self.subTest(name):
                self.assert_error(self.client.mutate("POST", "/api/v1/leads", body), 422, "VALIDATION_FAILED")
        for name, raw in {"truncated": '{"label": ', "not json": "hello", "array": "[1]", "empty": ""}.items():
            with self.subTest(name):
                self.assert_error(self.client.mutate("POST", "/api/v1/leads", raw), 400, "MALFORMED_REQUEST")
        self.assertEqual(self.counts()["lead"], 0)
        self.assertEqual(self.counts()["idempotency"], 1)  # only the profile save

    def test_fields_map_names_the_problem_fields(self):
        response = self.client.mutate("POST", "/api/v1/leads", {"label": "", "bogus": 1})
        fields = response.json()["error"]["fields"]
        self.assertEqual(fields["bogus"], "Unexpected field.")
        self.assertEqual(fields["country_tag"], "Required.")
        self.assertIn("label", fields)

    def test_create_without_profile_conflicts(self):
        other = self.start(data_dir=self.data_dir.parent / "no-profile").client
        response = other.create_lead()
        self.assert_error(response, 409, "PROFILE_REQUIRED")
        self.assertEqual(table_counts(self.data_dir.parent / "no-profile")["lead"], 0)

    def test_detail_and_missing_lead(self):
        lead = self.make("Fictional", "NL", "remote")
        detail = self.client.get(f"/api/v1/leads/{lead['id']}").json()
        self.assertEqual(detail["lead"], lead)
        self.assertEqual(detail["activities"], [])
        self.assertEqual(detail["business_date"], "2030-04-10")
        self.assert_error(self.client.get("/api/v1/leads/999"), 404, "NOT_FOUND")
        self.assert_error(self.client.get("/api/v1/leads/99999999999999999999999"), 404, "NOT_FOUND")

    def test_bad_filters_are_422(self):
        for query in ("?country=nl", "?bucket=soon", "?whatever=1", "?stage=a&stage=new", "?q=" + "x" * 121):
            with self.subTest(query):
                self.assert_error(self.client.get("/api/v1/leads" + query), 422, "VALIDATION_FAILED")

    def test_patch_updates_descriptive_fields(self):
        lead = self.make("Fictional", "NL", "remote", source_label="Board")
        response = self.client.mutate("PATCH", f"/api/v1/leads/{lead['id']}",
                                      {"version": 1, "label": "Renamed", "source_label": None})
        self.assertEqual(response.status, 200)
        updated = response.json()["lead"]
        self.assertEqual((updated["label"], updated["source_label"], updated["version"]), ("Renamed", None, 2))
        for body in ({"version": 2}, {"version": 2, "stage": "contacted"}, {"version": 2, "next_action": None},
                     {"version": 2, "label": ""}):
            with self.subTest(body):
                self.assert_error(self.client.mutate("PATCH", f"/api/v1/leads/{lead['id']}", body),
                                  422, "VALIDATION_FAILED")
        self.assert_error(self.client.mutate("PATCH", f"/api/v1/leads/{lead['id']}", {"version": 1, "label": "x"}),
                          409, "STALE_VERSION")
        self.assert_error(self.client.mutate("PATCH", "/api/v1/leads/999", {"version": 1, "label": "x"}),
                          404, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
