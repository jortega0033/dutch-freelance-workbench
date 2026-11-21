"""Strict validation of request payloads and query strings.

Every parse_* function takes the already-decoded JSON object (a dict) and
returns clean values, or raises ApiError(422, VALIDATION_FAILED) with a
{field: message} map. Unexpected keys are rejected; strings are trimmed;
types are strict (a bool is not an int, a number is not a string).
"""

import re

from . import dates
from .errors import ApiError

LABEL_MAX = 120
ACTION_MAX = 240
SUMMARY_MAX = 1000

COUNTRY_TAGS = ("NL", "other", "unknown")
WORK_MODES = ("remote", "hybrid", "onsite", "unknown")
SOURCE_CHANNELS = ("manual", "direct", "platform", "referral", "other")
STAGES = ("new", "researching", "contacted")
ACTIVITY_KINDS = ("outreach", "note")
BUCKETS = ("overdue", "today", "upcoming", "no_action")

_MAX_VERSION = 2**62


class Problems:
    """Collects field errors; the first message per field wins."""

    def __init__(self):
        self.fields = {}

    def add(self, field, message):
        self.fields.setdefault(field, message)

    def raise_if_any(self):
        if self.fields:
            raise ApiError(
                422, "VALIDATION_FAILED", "Some fields need attention.", fields=dict(self.fields)
            )


# --- single-value checks: each returns the clean value or None (and records a problem)


# C0 controls (except tab, line feed, carriage return) and DEL
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def check_text(value, field, problems, max_len):
    message = f"Enter 1–{max_len} characters."
    if not isinstance(value, str):
        problems.add(field, message)
        return None
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= max_len:
        problems.add(field, message)
        return None
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError:
        problems.add(field, "Enter valid text.")
        return None
    if _CONTROL_CHARS.search(cleaned):
        # NUL would also defeat SQLite's length() CHECK constraints
        problems.add(field, "Remove control characters from the text.")
        return None
    return cleaned


def check_enum(value, field, problems, allowed):
    if not isinstance(value, str) or value not in allowed:
        problems.add(field, "Choose one of: " + ", ".join(allowed))
        return None
    return value


def check_version(value, field, problems, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= _MAX_VERSION:
        problems.add(field, f"Enter a whole number of at least {minimum}.")
        return None
    return value


def check_date(value, field, problems):
    try:
        return dates.parse_date(value)
    except dates.DateError as exc:
        problems.add(field, str(exc))
        return None


def check_keys(body, allowed, required, problems):
    for key in body:
        if key not in allowed:
            problems.add(str(key)[:60], "Unexpected field.")
    for key in required:
        if key not in body:
            problems.add(key, "Required.")


def check_next_action(value, field, problems):
    """A {"text", "due_date"} object -> {"text": str, "due_date": "YYYY-MM-DD"} or None."""
    if not isinstance(value, dict):
        problems.add(field, "Provide the next action as text and a due date.")
        return None
    inner = Problems()
    check_keys(value, {"text", "due_date"}, {"text", "due_date"}, inner)
    text = check_text(value.get("text"), "text", inner, ACTION_MAX) if "text" in value else None
    due = check_date(value.get("due_date"), "due_date", inner) if "due_date" in value else None
    for name, message in inner.fields.items():
        problems.add(f"{field}.{name}", message)
    if inner.fields:
        return None
    return {"text": text, "due_date": due.isoformat()}


def _optional_source_label(value, field, problems):
    """null or blank -> None; otherwise trimmed 1..120 text."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return check_text(value, field, problems, LABEL_MAX)


# --- payload parsers


def parse_empty_body(body):
    """Commands that take no fields accept only {}."""
    problems = Problems()
    check_keys(body, set(), set(), problems)
    problems.raise_if_any()


def parse_profile(body):
    """-> (version, display_label)"""
    problems = Problems()
    check_keys(body, {"version", "display_label"}, {"version", "display_label"}, problems)
    version = check_version(body["version"], "version", problems, 0) if "version" in body else None
    label = (
        check_text(body["display_label"], "display_label", problems, LABEL_MAX)
        if "display_label" in body
        else None
    )
    problems.raise_if_any()
    return version, label


def parse_lead_create(body):
    """-> {label, country_tag, work_mode, source_channel, source_label}"""
    problems = Problems()
    required = {"label", "country_tag", "work_mode", "source_channel"}
    check_keys(body, required | {"source_label"}, required, problems)
    clean = {
        "label": check_text(body["label"], "label", problems, LABEL_MAX) if "label" in body else None,
        "country_tag": check_enum(body["country_tag"], "country_tag", problems, COUNTRY_TAGS)
        if "country_tag" in body
        else None,
        "work_mode": check_enum(body["work_mode"], "work_mode", problems, WORK_MODES)
        if "work_mode" in body
        else None,
        "source_channel": check_enum(body["source_channel"], "source_channel", problems, SOURCE_CHANNELS)
        if "source_channel" in body
        else None,
        "source_label": _optional_source_label(body.get("source_label"), "source_label", problems),
    }
    problems.raise_if_any()
    return clean


def parse_lead_patch(body):
    """-> (version, {changed descriptive columns})"""
    problems = Problems()
    descriptive = {"label", "country_tag", "work_mode", "source_channel", "source_label"}
    check_keys(body, descriptive | {"version"}, {"version"}, problems)
    version = check_version(body["version"], "version", problems, 1) if "version" in body else None
    changes = {}
    if "label" in body:
        changes["label"] = check_text(body["label"], "label", problems, LABEL_MAX)
    if "country_tag" in body:
        changes["country_tag"] = check_enum(body["country_tag"], "country_tag", problems, COUNTRY_TAGS)
    if "work_mode" in body:
        changes["work_mode"] = check_enum(body["work_mode"], "work_mode", problems, WORK_MODES)
    if "source_channel" in body:
        changes["source_channel"] = check_enum(
            body["source_channel"], "source_channel", problems, SOURCE_CHANNELS
        )
    if "source_label" in body:
        changes["source_label"] = _optional_source_label(body["source_label"], "source_label", problems)
    if not changes and not problems.fields:
        problems.add("body", "Provide at least one field to change.")
    problems.raise_if_any()
    return version, changes


def parse_activity(body, today):
    """-> {version, kind, business_date (str), summary, next_action (dict, or None if absent)}

    `today` is the Amsterdam business date (datetime.date) from the clock.
    """
    problems = Problems()
    required = {"version", "kind", "business_date", "summary"}
    check_keys(body, required | {"next_action"}, required, problems)
    clean = {}
    clean["version"] = check_version(body["version"], "version", problems, 1) if "version" in body else None
    clean["kind"] = check_enum(body["kind"], "kind", problems, ACTIVITY_KINDS) if "kind" in body else None
    day = check_date(body["business_date"], "business_date", problems) if "business_date" in body else None
    if day is not None and day > today:
        problems.add(
            "business_date",
            f"The activity date cannot be later than today ({today.isoformat()} in Amsterdam).",
        )
        day = None
    clean["business_date"] = day.isoformat() if day else None
    clean["summary"] = (
        check_text(body["summary"], "summary", problems, SUMMARY_MAX) if "summary" in body else None
    )
    clean["next_action"] = None
    if "next_action" in body:
        if body["next_action"] is None:
            problems.add("next_action", "A follow-up cannot be cleared here; provide text and a due date.")
        else:
            clean["next_action"] = check_next_action(body["next_action"], "next_action", problems)
    problems.raise_if_any()
    return clean


def parse_next_action_put(body):
    """-> (version, next_action dict, or None meaning 'clear')"""
    problems = Problems()
    check_keys(body, {"version", "next_action"}, {"version", "next_action"}, problems)
    version = check_version(body["version"], "version", problems, 1) if "version" in body else None
    action = None
    if "next_action" in body and body["next_action"] is not None:
        action = check_next_action(body["next_action"], "next_action", problems)
    problems.raise_if_any()
    return version, action


def parse_stage(body):
    """-> (version, stage, next_action dict, or None if absent)"""
    problems = Problems()
    check_keys(body, {"version", "stage", "next_action"}, {"version", "stage"}, problems)
    version = check_version(body["version"], "version", problems, 1) if "version" in body else None
    stage = check_enum(body["stage"], "stage", problems, STAGES) if "stage" in body else None
    action = None
    if "next_action" in body:
        if body["next_action"] is None:
            problems.add("next_action", "Provide text and a due date, or leave the field out.")
        else:
            action = check_next_action(body["next_action"], "next_action", problems)
    problems.raise_if_any()
    return version, stage, action


# --- query string for GET /api/v1/leads

_FILTER_ENUMS = {
    "stage": STAGES,
    "country": COUNTRY_TAGS,
    "mode": WORK_MODES,
    "source": SOURCE_CHANNELS,
    "bucket": BUCKETS,
}


def parse_lead_filters(query):
    """`query` is {name: [values]} from parse_qs(keep_blank_values=True).

    Returns only the filters that are set. Unknown names, repeated names and
    bad values are 422 errors.
    """
    problems = Problems()
    clean = {}
    for name, values in query.items():
        if name != "q" and name not in _FILTER_ENUMS:
            problems.add(name[:60], "Unexpected parameter.")
            continue
        if len(values) != 1:
            problems.add(name, "Give this filter once.")
            continue
        value = values[0]
        if name == "q":
            text = value.strip()
            if len(text) > LABEL_MAX:
                problems.add("q", f"Enter at most {LABEL_MAX} characters.")
            elif text:
                clean["q"] = text
        elif value != "":
            checked = check_enum(value, name, problems, _FILTER_ENUMS[name])
            if checked is not None:
                clean[name] = checked
    problems.raise_if_any()
    return clean
