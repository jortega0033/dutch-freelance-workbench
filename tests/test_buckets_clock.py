"""T11 buckets follow the clock; Amsterdam business date and DST behaviour."""

import datetime as dt
import sqlite3
import unittest

from tests.support.harness import WorkbenchTestCase, db_dump, db_file, parse_utc


class T11ClockBuckets(WorkbenchTestCase):
    def test_same_action_moves_between_buckets_without_any_write(self):
        running = self.start_with_profile()
        client, clock = running.client, running.clock
        lead_id = client.create_lead().json()["lead"]["id"]
        client.put_action(lead_id, 1, "Fictional follow-up", "2030-04-11")

        dump_before = db_dump(self.data_dir)
        conn = sqlite3.connect(str(db_file(self.data_dir)))
        self.addCleanup(conn.close)
        data_version_before = conn.execute("PRAGMA data_version").fetchone()[0]

        observed = []
        for now, expected in (("2030-04-10T10:00:00Z", "upcoming"), ("2030-04-11T10:00:00Z", "today"),
                              ("2030-04-12T10:00:00Z", "overdue"), ("2030-04-10T10:00:00Z", "upcoming")):
            clock.set(now)
            dashboard = client.get("/api/v1/dashboard").json()
            self.assertEqual([row["id"] for row in dashboard["groups"][expected]], [lead_id], now)
            self.assertEqual(sum(dashboard["counts"][b] for b in ("overdue", "today", "upcoming", "no_action")), 1)
            self.assertEqual(client.lead(lead_id)["lead"]["bucket"], expected)
            self.assertEqual(client.get(f"/api/v1/leads?bucket={expected}").json()["total"], 1)
            observed.append(expected)

        self.assertEqual(observed, ["upcoming", "today", "overdue", "upcoming"])
        self.assertEqual(db_dump(self.data_dir), dump_before)
        self.assertEqual(conn.execute("PRAGMA data_version").fetchone()[0], data_version_before)

    def test_amsterdam_midnight_boundary_in_summer(self):
        running = self.start_with_profile()
        client, clock = running.client, running.clock
        lead_id = client.create_lead().json()["lead"]["id"]
        client.put_action(lead_id, 1, "Due on the tenth", "2030-04-10")
        for instant, business_date, bucket in (
            ("2030-04-09T21:59:59Z", "2030-04-09", "upcoming"),
            ("2030-04-09T22:00:00Z", "2030-04-10", "today"),
            ("2030-04-10T21:59:59Z", "2030-04-10", "today"),
            ("2030-04-10T22:00:00Z", "2030-04-11", "overdue"),
        ):
            with self.subTest(instant=instant):
                clock.set(instant)
                self.assertEqual(client.get("/api/v1/session").json()["business_date"], business_date)
                dashboard = client.get("/api/v1/dashboard").json()
                self.assertEqual(dashboard["business_date"], business_date)
                self.assertEqual(dashboard["counts"][bucket], 1)

    def test_winter_midnight_is_2300_utc(self):
        running = self.start()
        for instant, business_date in (("2030-01-15T22:59:59Z", "2030-01-15"), ("2030-01-15T23:00:00Z", "2030-01-16")):
            running.clock.set(instant)
            self.assertEqual(running.client.get("/api/v1/session").json()["business_date"], business_date)

    def test_activity_date_limit_follows_the_clock(self):
        running = self.start_with_profile()
        client, clock = running.client, running.clock
        lead_id = client.create_lead().json()["lead"]["id"]
        clock.set("2030-04-09T21:59:59Z")  # still the 9th in Amsterdam
        self.assertEqual(client.activity(lead_id, 1, "note", "2030-04-10").status, 422)
        clock.set("2030-04-09T22:00:00Z")  # now the 10th
        self.assertEqual(client.activity(lead_id, 1, "note", "2030-04-10").status, 201)


class T11DstSweeps(WorkbenchTestCase):
    """Sweep every 15 minutes across both DST changes of 2030."""

    CASES = (
        # (start, end exclusive, due date, [(first UTC instant of today, first instant of overdue)])
        ("2030-03-29T00:00:00Z", "2030-04-02T00:00:00Z", "2030-03-31", "2030-03-30T23:00:00Z", "2030-03-31T22:00:00Z"),
        ("2030-10-25T00:00:00Z", "2030-10-30T00:00:00Z", "2030-10-27", "2030-10-26T22:00:00Z", "2030-10-27T23:00:00Z"),
    )

    def sweep(self, start, end, due):
        running = self.start(data_dir=self.data_dir.parent / f"sweep-{due}")
        client, clock = running.client, running.clock
        client.put_profile()
        lead_id = client.create_lead().json()["lead"]["id"]
        client.put_action(lead_id, 1, "Due on the DST day", due)
        step = dt.timedelta(minutes=15)
        instant, last = parse_utc(start), parse_utc(end)
        rows = []
        while instant < last:
            clock.set(instant)
            dashboard = client.get("/api/v1/dashboard").json()
            memberships = [b for b in ("overdue", "today", "upcoming", "no_action")
                           if any(row["id"] == lead_id for row in dashboard["groups"][b])]
            rows.append((instant, dashboard["business_date"], memberships))
            instant += step
        return rows

    def test_dst_sweeps(self):
        for start, end, due, today_from, overdue_from in self.CASES:
            with self.subTest(due=due):
                rows = self.sweep(start, end, due)
                dates_seen = [dt.date.fromisoformat(row[1]) for row in rows]
                self.assertEqual(dates_seen, sorted(dates_seen), "business date must never go backwards")
                first, last = dates_seen[0], dates_seen[-1]
                every_day = {first + dt.timedelta(days=n) for n in range((last - first).days + 1)}
                self.assertEqual(set(dates_seen), every_day, "every calendar date appears")
                self.assertTrue(dt.date.fromisoformat(due) in every_day)

                for instant, business_date, memberships in rows:
                    self.assertEqual(len(memberships), 1, (instant, memberships))
                sequence = [row[2][0] for row in rows]
                compressed = [b for i, b in enumerate(sequence) if i == 0 or sequence[i - 1] != b]
                self.assertEqual(compressed, ["upcoming", "today", "overdue"], "each transition happens exactly once")

                first_today = next(row[0] for row in rows if row[2] == ["today"])
                first_overdue = next(row[0] for row in rows if row[2] == ["overdue"])
                self.assertEqual(first_today, parse_utc(today_from))
                self.assertEqual(first_overdue, parse_utc(overdue_from))
                # Amsterdam day lengths: 23 hours in spring, 25 hours in autumn
                hours = (first_overdue - first_today).total_seconds() / 3600
                self.assertEqual(hours, 23 if due == "2030-03-31" else 25)


if __name__ == "__main__":
    unittest.main()
