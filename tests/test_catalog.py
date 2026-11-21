"""Issue #1: browse the bundled fictional catalog and save an item as a lead."""

import json
import threading
from pathlib import Path

from tests.support.harness import WorkbenchTestCase, db_query, new_key
from workbench import discovery

CATALOG = json.loads(discovery.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))["items"]
FIRST = CATALOG[0]


def save_path(key):
    return f"/api/v1/catalog/items/{key}/lead"


class BundledCatalogFile(WorkbenchTestCase):
    def test_bundled_catalog_is_valid_and_fictional(self):
        items = discovery.parse_catalog(json.loads(discovery.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8")))
        self.assertGreaterEqual(len(items), 10)
        for item in items:
            self.assertIn("fictional", (item["organisation"] + item["title"]).lower(), item["key"])
        # every filter value is represented, so each filter can be exercised
        self.assertEqual({i["country_tag"] for i in items}, {"NL", "other", "unknown"})
        self.assertEqual({i["engagement_type"] for i in items}, set(discovery.ENGAGEMENT_TYPES))
        self.assertTrue({"remote", "hybrid", "onsite"} <= {i["work_mode"] for i in items})

    def test_invalid_documents_are_rejected(self):
        good = json.loads(discovery.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
        bad_documents = []
        for change in (
            lambda d: d.update(format=2),
            lambda d: d.update(extra=1),
            lambda d: d["items"].append(dict(d["items"][0])),          # duplicate key
            lambda d: d["items"][0].update(key="CAT 1"),
            lambda d: d["items"][0].update(title=""),
            lambda d: d["items"][0].update(title="x" * 121),
            lambda d: d["items"][0].update(skills=[]),
            lambda d: d["items"][0].update(engagement_type="full-time"),
            lambda d: d["items"][0].update(country_tag="DE"),
            lambda d: d["items"][0].update(work_mode="anywhere"),
            lambda d: d["items"][0].update(surprise=True),
        ):
            document = json.loads(json.dumps(good))
            change(document)
            bad_documents.append(document)
        for document in bad_documents:
            with self.subTest(document=str(document)[:80]):
                with self.assertRaises(discovery.CatalogError):
                    discovery.parse_catalog(document)


class Browse(WorkbenchTestCase):
    def expected(self, **filters):
        def ok(item):
            return (("skill" not in filters or filters["skill"] in item["skills"])
                    and ("engagement" not in filters or item["engagement_type"] == filters["engagement"])
                    and ("country" not in filters or item["country_tag"] == filters["country"])
                    and ("mode" not in filters or item["work_mode"] == filters["mode"]))
        return [item["key"] for item in CATALOG if ok(item)]

    def keys(self, client, query=""):
        response = client.get("/api/v1/catalog" + query)
        self.assertEqual(response.status, 200, response.raw[:200])
        return [item["key"] for item in response.json()["items"]]

    def test_each_filter_and_their_and_combination(self):
        client = self.start().client
        self.assertEqual(self.keys(client), [item["key"] for item in CATALOG])
        cases = [
            {"skill": "Python"}, {"skill": "Accessibility"}, {"engagement": "retainer"},
            {"engagement": "fixed-scope"}, {"country": "NL"}, {"country": "unknown"}, {"mode": "remote"},
            {"skill": "Python", "country": "NL"}, {"country": "NL", "mode": "remote"},
            {"skill": "TypeScript", "engagement": "hourly-contract", "country": "NL", "mode": "hybrid"},
            {"skill": "Django", "country": "other"},
        ]
        for filters in cases:
            with self.subTest(filters=filters):
                query = "?" + "&".join(f"{k}={v}" for k, v in filters.items())
                self.assertEqual(self.keys(client, query), self.expected(**filters))
        self.assertEqual(self.keys(client, "?skill=Django&country=other"), [], "AND, not OR")
        self.assertEqual(self.keys(client, "?skill=Data%20engineering"), self.expected(skill="Data engineering"))
        self.assertEqual(self.keys(client, "?skill=&country="), [item["key"] for item in CATALOG])

    def test_facets_and_shape(self):
        data = self.start().client.get("/api/v1/catalog").json()
        self.assertEqual(data["catalog_total"], len(CATALOG))
        self.assertEqual(data["facets"]["engagement_types"], list(discovery.ENGAGEMENT_TYPES))
        self.assertIn("Python", data["facets"]["skills"])
        self.assertEqual(data["items"][0], dict(FIRST, saved_lead=None))

    def test_bad_filters_are_rejected(self):
        client = self.start().client
        for query in ("?skill=Cobol", "?engagement=full-time", "?country=DE", "?mode=anywhere", "?q=x",
                      "?skill=Python&skill=SQL", "?country=nl"):
            with self.subTest(query=query):
                self.assert_error(client.get("/api/v1/catalog" + query), 422, "VALIDATION_FAILED")

    def test_browsing_creates_nothing(self):
        running = self.start_with_profile()
        before = self.snapshot()
        for query in ("", "?skill=Python", "?country=NL&mode=remote", "?skill=Cobol"):
            running.client.get("/api/v1/catalog" + query)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.counts()["lead"], 0)


class SaveAsLead(WorkbenchTestCase):
    def test_save_uses_lead_creation_rules_and_stores_the_key(self):
        client = self.start_with_profile().client
        response = client.mutate("POST", save_path(FIRST["key"]), {})
        self.assertEqual(response.status, 201, response.raw[:300])
        body = response.json()
        self.assertTrue(body["created"])
        lead = body["lead"]
        self.assertEqual((lead["label"], lead["country_tag"], lead["work_mode"]),
                         (FIRST["title"], FIRST["country_tag"], FIRST["work_mode"]))
        self.assertEqual((lead["source_channel"], lead["source_label"]), ("platform", FIRST["organisation"]))
        self.assertEqual((lead["stage"], lead["version"], lead["next_action"], lead["bucket"]),
                         ("new", 1, None, "no_action"))
        self.assertEqual(lead["catalog_key"], FIRST["key"])
        self.assertEqual(db_query(self.data_dir, "SELECT command_type, entity_id, resulting_version, from_stage, to_stage"
                                                 " FROM audit WHERE entity_type = 'lead'"),
                         [("lead.create", lead["id"], 1, None, "new")])
        listed = client.get("/api/v1/catalog").json()["items"][0]
        self.assertEqual(listed["saved_lead"], {"id": lead["id"], "label": FIRST["title"]})
        # the saved lead is an ordinary lead: it shows up in lists and on the dashboard
        self.assertEqual(client.get("/api/v1/leads?source=platform").json()["total"], 1)
        self.assertEqual(client.get("/api/v1/dashboard").json()["counts"]["no_action"], 1)

    def test_retry_with_the_same_key_replays(self):
        client = self.start_with_profile().client
        key = new_key()
        first = client.mutate("POST", save_path(FIRST["key"]), {}, key=key)
        counts = self.counts()
        again = client.mutate("POST", save_path(FIRST["key"]), {}, key=key)
        self.assertEqual((again.status, again.text), (first.status, first.text))
        self.assertEqual(again.headers.get("idempotent-replayed"), "true")
        self.assertEqual(self.counts(), counts)

    def test_second_save_with_a_new_key_returns_the_existing_lead(self):
        client = self.start_with_profile().client
        first = client.mutate("POST", save_path(FIRST["key"]), {}).json()["lead"]
        lead_rows = db_query(self.data_dir, "SELECT * FROM lead")
        second = client.mutate("POST", save_path(FIRST["key"]), {})
        self.assertEqual(second.status, 200)
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.json()["lead"]["id"], first["id"])
        self.assertEqual(db_query(self.data_dir, "SELECT * FROM lead"), lead_rows)
        self.assertEqual(self.counts()["audit"], 2)  # profile.save + one lead.create

    def test_saved_lead_keeps_its_link_after_edits(self):
        client = self.start_with_profile().client
        lead = client.mutate("POST", save_path(FIRST["key"]), {}).json()["lead"]
        self.assertEqual(client.mutate("PATCH", f"/api/v1/leads/{lead['id']}",
                                       {"version": 1, "label": "Renamed fictional lead"}).status, 200)
        again = client.mutate("POST", save_path(FIRST["key"]), {}).json()
        self.assertEqual((again["created"], again["lead"]["id"], again["lead"]["label"]),
                         (False, lead["id"], "Renamed fictional lead"))

    def test_concurrent_saves_create_one_lead(self):
        running = self.start_with_profile()
        start = threading.Barrier(8)
        statuses = []

        def worker():
            start.wait()
            statuses.append(running.client.mutate("POST", save_path(FIRST["key"]), {}).status)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(sorted(statuses), [200] * 7 + [201])
        self.assertEqual(db_query(self.data_dir, "SELECT COUNT(*) FROM lead WHERE catalog_key = ?", (FIRST["key"],)),
                         [(1,)])
        self.assertEqual(self.counts()["lead"], 1)

    def test_unique_index_backs_up_the_rule(self):
        self.start_with_profile().client.mutate("POST", save_path(FIRST["key"]), {})
        with self.assertRaises(Exception):
            db_query(self.data_dir, "INSERT INTO lead (label, country_tag, work_mode, source_channel, stage, version,"
                                    " created_at_utc, updated_at_utc, catalog_key) VALUES"
                                    " ('x', 'NL', 'remote', 'platform', 'new', 1, 't', 't', ?)", (FIRST["key"],))

    def test_manual_leads_have_no_catalog_key(self):
        client = self.start_with_profile().client
        lead = client.create_lead("Fictional manual lead").json()["lead"]
        self.assertIsNone(lead["catalog_key"])

    def test_rejections_write_nothing(self):
        running = self.start()
        client = running.client
        before = self.snapshot()
        self.assert_error(client.mutate("POST", save_path(FIRST["key"]), {}), 409, "PROFILE_REQUIRED")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(client.put_profile().status, 200)
        before = self.snapshot()
        self.assert_error(client.mutate("POST", save_path("cat-does-not-exist"), {}), 404, "NOT_FOUND")
        self.assert_error(client.mutate("POST", "/api/v1/catalog/items/CAT_1/lead", {}), 404, "NOT_FOUND")
        response = client.mutate("POST", save_path(FIRST["key"]), {"label": "override"})
        self.assert_error(response, 422, "VALIDATION_FAILED")
        self.assertIn("label", response.json()["error"]["fields"])
        self.assert_error(client.mutate("POST", save_path(FIRST["key"]), {}, origin="http://evil.example"),
                          403, "ORIGIN_NOT_ALLOWED")
        self.assert_error(client.mutate("POST", save_path(FIRST["key"]), {}, token="wrong"), 403, "SESSION_INVALID")
        self.assertEqual(client.request("GET", save_path(FIRST["key"])).status, 405)
        self.assertEqual(self.snapshot(), before)

    def test_failure_before_commit_rolls_back_then_retry_succeeds_once(self):
        running = self.start_with_profile()
        client = running.client
        before = self.snapshot()
        key = new_key()
        running.faults.arm("before_commit")
        self.assert_error(client.mutate("POST", save_path(FIRST["key"]), {}, key=key), 500, "INTERNAL_ERROR")
        self.assertEqual(self.snapshot(), before)
        running.faults.disarm()
        self.assertEqual(client.mutate("POST", save_path(FIRST["key"]), {}, key=key).status, 201)
        self.assertEqual(client.mutate("POST", save_path(FIRST["key"]), {}, key=key).status, 201)  # replay
        self.assertEqual(self.counts()["lead"], 1)


class CatalogUnavailable(WorkbenchTestCase):
    """If the catalog cannot load, Discover says so and manual capture keeps working."""

    def check(self, catalog_path):
        running = self.start_with_profile(catalog_path=catalog_path)
        client = running.client
        self.assert_error(client.get("/api/v1/catalog"), 503, "CATALOG_UNAVAILABLE")
        before = self.snapshot()
        self.assert_error(client.mutate("POST", save_path(FIRST["key"]), {}), 503, "CATALOG_UNAVAILABLE")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(client.create_lead("Fictional manual lead").status, 201)
        self.assertEqual(client.get("/api/v1/dashboard").status, 200)
        self.assertEqual(client.get("/discover").status, 200)

    def test_missing_file(self):
        self.check(Path(self.data_dir).parent / "no-such-catalog.json")

    def test_invalid_json(self):
        path = Path(self.data_dir).parent / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        self.check(path)

    def test_invalid_item(self):
        path = Path(self.data_dir).parent / "invalid.json"
        document = json.loads(discovery.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
        document["items"][0]["country_tag"] = "DE"
        path.write_text(json.dumps(document), encoding="utf-8")
        self.check(path)

    def test_a_repaired_file_is_picked_up_without_restart(self):
        path = Path(self.data_dir).parent / "later.json"
        running = self.start_with_profile(catalog_path=path)
        self.assert_error(running.client.get("/api/v1/catalog"), 503, "CATALOG_UNAVAILABLE")
        path.write_text(discovery.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(running.client.get("/api/v1/catalog").status, 200)
