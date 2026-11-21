"""Clock, Amsterdam business date and strict date parsing.

The business date is the calendar date in Europe/Amsterdam. This machine may
have no tz database, so when zoneinfo cannot load the zone we fall back to the
EU summer-time rule: UTC+2 from 01:00 UTC on the last Sunday of March
(inclusive) until 01:00 UTC on the last Sunday of October (exclusive), else
UTC+1. That rule is correct for 1996 onwards.
"""

import datetime as _dt
import re

_UTC = _dt.timezone.utc
TIMEZONE_NAME = "Europe/Amsterdam"
DATE_MESSAGE = "Enter a real date as YYYY-MM-DD between 2000-01-01 and 2099-12-31."

try:  # pragma: no cover - depends on the machine
    from zoneinfo import ZoneInfo

    AMSTERDAM_ZONE = ZoneInfo(TIMEZONE_NAME)
except Exception:  # no tz database installed
    AMSTERDAM_ZONE = None


class SystemClock:
    def now_utc(self):
        return _dt.datetime.now(_UTC)


class DateError(ValueError):
    """Raised by parse_date; the message is safe to show to users."""


def _last_sunday_1am_utc(year, month):
    day = _dt.date(year, month, 31)  # March and October both have 31 days
    days_since_sunday = (day.weekday() + 1) % 7  # Monday=0 .. Sunday=6
    day -= _dt.timedelta(days=days_since_sunday)
    return _dt.datetime(day.year, day.month, day.day, 1, tzinfo=_UTC)


def _eu_rule_offset(utc_dt):
    """Amsterdam UTC offset (timedelta) at an aware datetime, by the EU rule."""
    utc_dt = utc_dt.astimezone(_UTC)
    start = _last_sunday_1am_utc(utc_dt.year, 3)
    end = _last_sunday_1am_utc(utc_dt.year, 10)
    hours = 2 if start <= utc_dt < end else 1
    return _dt.timedelta(hours=hours)


def amsterdam_date_by_rule(utc_dt):
    utc_dt = utc_dt.astimezone(_UTC)
    return (utc_dt + _eu_rule_offset(utc_dt)).date()


def business_date(now_utc):
    """Amsterdam calendar date (datetime.date) for an aware UTC datetime."""
    if now_utc.tzinfo is None:
        raise ValueError("business_date needs a timezone-aware datetime")
    if AMSTERDAM_ZONE is not None:
        return now_utc.astimezone(AMSTERDAM_ZONE).date()
    return amsterdam_date_by_rule(now_utc)


_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def parse_date(value):
    """Parse exactly YYYY-MM-DD (real date, year 2000..2099) into a date."""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise DateError(DATE_MESSAGE)
    try:
        parsed = _dt.date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        raise DateError(DATE_MESSAGE) from None
    if not 2000 <= parsed.year <= 2099:
        raise DateError(DATE_MESSAGE)
    return parsed


def utc_stamp(moment):
    """Storage format for UTC timestamps: YYYY-MM-DDTHH:MM:SS.ffffffZ."""
    return moment.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
