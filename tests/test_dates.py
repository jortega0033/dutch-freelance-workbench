import datetime as dt
import unittest

from workbench import dates

UTC = dt.timezone.utc


def utc(*args):
    return dt.datetime(*args, tzinfo=UTC)


def last_sunday(year, month):
    """Independent computation of the last Sunday of March/October."""
    day = dt.date(year, month, 31)
    while day.weekday() != 6:
        day -= dt.timedelta(days=1)
    return day


class ParseDateTests(unittest.TestCase):
    def test_valid_dates(self):
        self.assertEqual(dates.parse_date("2030-04-10"), dt.date(2030, 4, 10))
        self.assertEqual(dates.parse_date("2000-01-01"), dt.date(2000, 1, 1))
        self.assertEqual(dates.parse_date("2099-12-31"), dt.date(2099, 12, 31))

    def test_leap_years(self):
        self.assertEqual(dates.parse_date("2028-02-29"), dt.date(2028, 2, 29))
        self.assertEqual(dates.parse_date("2000-02-29"), dt.date(2000, 2, 29))
        for bad in ("2030-02-29", "2030-02-30", "2029-02-29", "2030-04-31", "2030-13-01", "2030-00-10", "2030-01-00"):
            with self.subTest(bad=bad):
                with self.assertRaises(dates.DateError):
                    dates.parse_date(bad)

    def test_format_is_strict(self):
        for bad in ("2030-4-1", "30-04-10", "2030/04/10", "20300410", " 2030-04-10", "2030-04-10 ",
                    "2030-04-10\n", "2030-04-10T00:00:00", "", "2030-04", "٢٠٣٠-04-10"):
            with self.subTest(bad=bad):
                with self.assertRaises(dates.DateError):
                    dates.parse_date(bad)

    def test_year_range(self):
        for bad in ("1999-12-31", "2100-01-01", "0000-01-01", "9999-12-31"):
            with self.subTest(bad=bad):
                with self.assertRaises(dates.DateError):
                    dates.parse_date(bad)

    def test_non_strings(self):
        for bad in (None, 20300410, 2030.0, True, [], {}, dt.date(2030, 4, 10)):
            with self.subTest(bad=bad):
                with self.assertRaises(dates.DateError):
                    dates.parse_date(bad)

    def test_message(self):
        with self.assertRaises(dates.DateError) as ctx:
            dates.parse_date("nope")
        self.assertEqual(str(ctx.exception), "Enter a real date as YYYY-MM-DD between 2000-01-01 and 2099-12-31.")


class UtcStampTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(dates.utc_stamp(utc(2030, 4, 10, 9, 8, 7, 123)), "2030-04-10T09:08:07.000123Z")

    def test_converts_other_zones(self):
        plus_two = dt.timezone(dt.timedelta(hours=2))
        moment = dt.datetime(2030, 4, 10, 12, 0, tzinfo=plus_two)
        self.assertEqual(dates.utc_stamp(moment), "2030-04-10T10:00:00.000000Z")


class EuRuleTests(unittest.TestCase):
    YEARS = (2000, 2024, 2030, 2099)
    ONE_HOUR = dt.timedelta(hours=1)
    TWO_HOURS = dt.timedelta(hours=2)

    def test_known_transition_days(self):
        self.assertEqual(last_sunday(2030, 3), dt.date(2030, 3, 31))
        self.assertEqual(last_sunday(2030, 10), dt.date(2030, 10, 27))
        self.assertEqual(last_sunday(2024, 3), dt.date(2024, 3, 31))
        self.assertEqual(last_sunday(2024, 10), dt.date(2024, 10, 27))
        self.assertEqual(last_sunday(2000, 3), dt.date(2000, 3, 26))
        self.assertEqual(last_sunday(2000, 10), dt.date(2000, 10, 29))

    def test_offsets_around_transitions(self):
        one_second = dt.timedelta(seconds=1)
        for year in self.YEARS:
            spring = last_sunday(year, 3)
            autumn = last_sunday(year, 10)
            spring_at = utc(year, 3, spring.day, 1)
            autumn_at = utc(year, 10, autumn.day, 1)
            with self.subTest(year=year):
                self.assertEqual(dates._eu_rule_offset(spring_at - one_second), self.ONE_HOUR)
                self.assertEqual(dates._eu_rule_offset(spring_at), self.TWO_HOURS)
                self.assertEqual(dates._eu_rule_offset(autumn_at - one_second), self.TWO_HOURS)
                self.assertEqual(dates._eu_rule_offset(autumn_at), self.ONE_HOUR)
                self.assertEqual(dates._eu_rule_offset(utc(year, 1, 15, 12)), self.ONE_HOUR)
                self.assertEqual(dates._eu_rule_offset(utc(year, 7, 15, 12)), self.TWO_HOURS)
                self.assertEqual(dates._eu_rule_offset(utc(year, 12, 15, 12)), self.ONE_HOUR)

    def test_business_date_at_amsterdam_midnights(self):
        # 2030: winter midnight is 23:00Z, summer midnight is 22:00Z
        cases = [
            (utc(2030, 1, 10, 22, 59, 59), dt.date(2030, 1, 10)),
            (utc(2030, 1, 10, 23, 0, 0), dt.date(2030, 1, 11)),
            (utc(2030, 7, 10, 21, 59, 59), dt.date(2030, 7, 10)),
            (utc(2030, 7, 10, 22, 0, 0), dt.date(2030, 7, 11)),
            (utc(2030, 3, 30, 22, 59, 59), dt.date(2030, 3, 30)),
            (utc(2030, 3, 30, 23, 0, 0), dt.date(2030, 3, 31)),
            (utc(2030, 3, 31, 21, 59, 59), dt.date(2030, 3, 31)),
            (utc(2030, 3, 31, 22, 0, 0), dt.date(2030, 4, 1)),
            (utc(2030, 10, 26, 21, 59, 59), dt.date(2030, 10, 26)),
            (utc(2030, 10, 26, 22, 0, 0), dt.date(2030, 10, 27)),
            (utc(2030, 10, 27, 22, 59, 59), dt.date(2030, 10, 27)),
            (utc(2030, 10, 27, 23, 0, 0), dt.date(2030, 10, 28)),
        ]
        for instant, expected in cases:
            with self.subTest(instant=instant.isoformat()):
                self.assertEqual(dates.amsterdam_date_by_rule(instant), expected)

    def test_rule_accepts_other_offsets(self):
        plus_five = dt.timezone(dt.timedelta(hours=5))
        instant = dt.datetime(2030, 4, 10, 3, 0, tzinfo=plus_five)  # 22:00Z on 04-09
        self.assertEqual(dates.amsterdam_date_by_rule(instant), dt.date(2030, 4, 10))

    def test_rule_matches_zoneinfo_when_available(self):
        if dates.AMSTERDAM_ZONE is None:
            self.skipTest("no tz database on this machine")
        step = dt.timedelta(minutes=15)
        for year in self.YEARS:
            for month in (3, 10):
                start = utc(year, month, last_sunday(year, month).day) - dt.timedelta(days=2)
                for i in range(int(dt.timedelta(days=4) / step)):
                    instant = start + i * step
                    with self.subTest(instant=instant.isoformat()):
                        self.assertEqual(dates.amsterdam_date_by_rule(instant),
                                         instant.astimezone(dates.AMSTERDAM_ZONE).date())


class BusinessDateTests(unittest.TestCase):
    def test_requires_aware_datetime(self):
        with self.assertRaises(ValueError):
            dates.business_date(dt.datetime(2030, 4, 10, 12, 0))

    def test_boundary(self):
        self.assertEqual(dates.business_date(utc(2030, 4, 9, 21, 59, 59)), dt.date(2030, 4, 9))
        self.assertEqual(dates.business_date(utc(2030, 4, 9, 22, 0, 0)), dt.date(2030, 4, 10))

    def test_system_clock_is_aware_utc(self):
        now = dates.SystemClock().now_utc()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset(), dt.timedelta(0))


if __name__ == "__main__":
    unittest.main()
