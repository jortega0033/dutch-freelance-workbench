import datetime as dt
import unittest

from workbench import validation as v
from workbench.errors import ApiError

TODAY = dt.date(2030, 4, 10)


def fields_of(callable_, *args):
    """Call a parser that must fail validation; return its {field: message} map."""
    try:
        callable_(*args)
    except ApiError as err:
        assert err.status == 422 and err.code == "VALIDATION_FAILED", (err.status, err.code)
        return err.fields
    raise AssertionError("expected a validation failure")


class TextTests(unittest.TestCase):
    def check(self, value, max_len=120):
        problems = v.Problems()
        return v.check_text(value, "f", problems, max_len), problems.fields

    def test_trims_and_accepts(self):
        self.assertEqual(self.check("  Acme  ")[0], "Acme")
        self.assertEqual(self.check("x" * 120)[0], "x" * 120)

    def test_length_measured_after_trim(self):
        self.assertEqual(self.check(" " + "x" * 120 + " ")[0], "x" * 120)
        value, fields = self.check("x" * 121)
        self.assertIsNone(value)
        self.assertIn("f", fields)

    def test_blank_and_whitespace_rejected(self):
        for blank in ("", "   ", "\t\n", " "):
            with self.subTest(blank=repr(blank)):
                self.assertIsNone(self.check(blank)[0])

    def test_non_strings_rejected(self):
        for bad in (None, 5, 1.5, True, [], {}, b"x"):
            with self.subTest(bad=repr(bad)):
                self.assertIsNone(self.check(bad)[0])

    def test_lone_surrogate_rejected(self):
        self.assertIsNone(self.check("a\ud800b")[0])

    def test_markup_is_just_text(self):
        self.assertEqual(self.check("<img src=x onerror=alert(1)>")[0], "<img src=x onerror=alert(1)>")


class VersionTests(unittest.TestCase):
    def check(self, value, minimum):
        problems = v.Problems()
        return v.check_version(value, "version", problems, minimum), problems.fields

    def test_ints(self):
        self.assertEqual(self.check(0, 0)[0], 0)
        self.assertEqual(self.check(7, 1)[0], 7)

    def test_minimum(self):
        self.assertIsNone(self.check(0, 1)[0])
        self.assertIsNone(self.check(-1, 0)[0])

    def test_bool_rejected(self):
        self.assertIsNone(self.check(True, 0)[0])
        self.assertIsNone(self.check(False, 0)[0])

    def test_other_types_rejected(self):
        for bad in ("1", 1.0, 1.5, None, [1], {}):
            with self.subTest(bad=repr(bad)):
                self.assertIsNone(self.check(bad, 0)[0])

    def test_absurdly_large_rejected(self):
        self.assertIsNone(self.check(2**70, 0)[0])


class KeysTests(unittest.TestCase):
    def test_unexpected_and_missing(self):
        problems = v.Problems()
        v.check_keys({"a": 1, "zzz": 2}, {"a", "b"}, {"a", "b"}, problems)
        self.assertEqual(problems.fields, {"zzz": "Unexpected field.", "b": "Required."})


class ProfileTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(v.parse_profile({"version": 0, "display_label": " Me "}), (0, "Me"))

    def test_errors(self):
        self.assertIn("display_label", fields_of(v.parse_profile, {"version": 0, "display_label": "  "}))
        self.assertIn("version", fields_of(v.parse_profile, {"version": True, "display_label": "x"}))
        self.assertIn("version", fields_of(v.parse_profile, {"version": -1, "display_label": "x"}))
        self.assertEqual(fields_of(v.parse_profile, {}), {"version": "Required.", "display_label": "Required."})


class LeadCreateTests(unittest.TestCase):
    BASE = {"label": "Acme", "country_tag": "NL", "work_mode": "remote", "source_channel": "manual"}

    def test_defaults_source_label_none(self):
        self.assertIsNone(v.parse_lead_create(self.BASE)["source_label"])

    def test_blank_source_label_becomes_none(self):
        for blank in (None, "", "   "):
            with self.subTest(blank=blank):
                self.assertIsNone(v.parse_lead_create({**self.BASE, "source_label": blank})["source_label"])

    def test_source_label_trimmed(self):
        self.assertEqual(v.parse_lead_create({**self.BASE, "source_label": " Board "})["source_label"], "Board")

    def test_source_label_too_long(self):
        self.assertIn("source_label", fields_of(v.parse_lead_create, {**self.BASE, "source_label": "x" * 121}))

    def test_enums(self):
        for field, bad in (("country_tag", "NLD"), ("work_mode", "Remote"), ("source_channel", "web")):
            with self.subTest(field=field):
                message = fields_of(v.parse_lead_create, {**self.BASE, field: bad})[field]
                self.assertTrue(message.startswith("Choose one of: "))

    def test_unexpected_and_missing(self):
        self.assertEqual(fields_of(v.parse_lead_create, {**self.BASE, "stage": "new"}), {"stage": "Unexpected field."})
        self.assertEqual(fields_of(v.parse_lead_create, {"label": "x"})["country_tag"], "Required.")


class PatchTests(unittest.TestCase):
    def test_needs_a_change(self):
        self.assertIn("body", fields_of(v.parse_lead_patch, {"version": 1}))

    def test_stage_not_patchable(self):
        self.assertEqual(fields_of(v.parse_lead_patch, {"version": 1, "stage": "new"}), {"stage": "Unexpected field."})

    def test_ok(self):
        self.assertEqual(v.parse_lead_patch({"version": 2, "label": " X ", "source_label": None}),
                         (2, {"label": "X", "source_label": None}))


class ActivityTests(unittest.TestCase):
    BASE = {"version": 1, "kind": "note", "business_date": "2030-04-10", "summary": " hi "}

    def test_ok(self):
        clean = v.parse_activity(self.BASE, TODAY)
        self.assertEqual(clean["summary"], "hi")
        self.assertEqual(clean["business_date"], "2030-04-10")
        self.assertIsNone(clean["next_action"])

    def test_future_date(self):
        message = fields_of(v.parse_activity, {**self.BASE, "business_date": "2030-04-11"}, TODAY)["business_date"]
        self.assertIn("cannot be later than today", message)

    def test_leap_day(self):
        today = dt.date(2028, 3, 1)
        ok = v.parse_activity({**self.BASE, "business_date": "2028-02-29"}, today)
        self.assertEqual(ok["business_date"], "2028-02-29")
        self.assertIn("business_date", fields_of(v.parse_activity, {**self.BASE, "business_date": "2030-02-29"}, TODAY))

    def test_bad_dates(self):
        for bad in ("2030-02-30", "2030-4-1", "1999-12-31", "2100-01-01", "", None, 20300410):
            with self.subTest(bad=bad):
                self.assertIn("business_date", fields_of(v.parse_activity, {**self.BASE, "business_date": bad}, TODAY))

    def test_next_action_shapes(self):
        ok = v.parse_activity({**self.BASE, "next_action": {"text": " Call ", "due_date": "2030-01-01"}}, TODAY)
        self.assertEqual(ok["next_action"], {"text": "Call", "due_date": "2030-01-01"})  # past allowed
        self.assertIn("next_action", fields_of(v.parse_activity, {**self.BASE, "next_action": None}, TODAY))
        self.assertIn("next_action", fields_of(v.parse_activity, {**self.BASE, "next_action": "x"}, TODAY))
        nested = fields_of(v.parse_activity, {**self.BASE, "next_action": {"text": "", "due_date": "bad", "x": 1}}, TODAY)
        self.assertEqual(set(nested), {"next_action.text", "next_action.due_date", "next_action.x"})

    def test_action_text_limit(self):
        long_action = {"text": "x" * 241, "due_date": "2030-05-01"}
        self.assertIn("next_action.text", fields_of(v.parse_activity, {**self.BASE, "next_action": long_action}, TODAY))

    def test_summary_limit(self):
        self.assertIn("summary", fields_of(v.parse_activity, {**self.BASE, "summary": "x" * 1001}, TODAY))
        self.assertEqual(v.parse_activity({**self.BASE, "summary": "x" * 1000}, TODAY)["summary"], "x" * 1000)

    def test_kind(self):
        self.assertIn("kind", fields_of(v.parse_activity, {**self.BASE, "kind": "call"}, TODAY))


class NextActionPutAndStageTests(unittest.TestCase):
    def test_put_null_means_clear(self):
        self.assertEqual(v.parse_next_action_put({"version": 1, "next_action": None}), (1, None))

    def test_put_requires_key(self):
        self.assertIn("next_action", fields_of(v.parse_next_action_put, {"version": 1}))

    def test_stage(self):
        self.assertEqual(v.parse_stage({"version": 1, "stage": "researching"}), (1, "researching", None))
        self.assertIn("stage", fields_of(v.parse_stage, {"version": 1, "stage": "done"}))
        self.assertIn("next_action", fields_of(v.parse_stage, {"version": 1, "stage": "researching", "next_action": None}))


class FilterTests(unittest.TestCase):
    def test_empty_means_no_filter(self):
        self.assertEqual(v.parse_lead_filters({"q": [""], "stage": [""], "bucket": [""]}), {})

    def test_values(self):
        self.assertEqual(v.parse_lead_filters({"q": ["  Acme "], "country": ["NL"], "bucket": ["today"]}),
                         {"q": "Acme", "country": "NL", "bucket": "today"})

    def test_rejections(self):
        self.assertIn("nope", fields_of(v.parse_lead_filters, {"nope": ["1"]}))
        self.assertIn("country", fields_of(v.parse_lead_filters, {"country": ["nl"]}))
        self.assertIn("stage", fields_of(v.parse_lead_filters, {"stage": ["a", "b"]}))
        self.assertIn("q", fields_of(v.parse_lead_filters, {"q": ["x" * 121]}))


if __name__ == "__main__":
    unittest.main()
