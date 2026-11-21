"""The bundled fictional opportunity catalog (Discover screen).

The catalog is a read-only JSON file shipped with the source. Browsing it
never writes anything. Saving an item as a lead is a write command in
leads.py (save_catalog_item) that reuses the normal lead-creation rules and
stores the item's stable key on the lead, so one catalog item maps to at
most one lead.

If the file is missing or invalid, catalog endpoints answer
503 CATALOG_UNAVAILABLE and everything else, including manual lead capture,
keeps working.
"""

import json
import re
import threading
from pathlib import Path

from . import validation
from .errors import ApiError

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent / "catalog" / "fictional-opportunities.json"
CATALOG_FORMAT = 1
KEY_PATTERN = re.compile(r"cat-[a-z0-9]+(?:-[a-z0-9]+)*")
KEY_MAX = 64
ENGAGEMENT_TYPES = ("hourly-contract", "fixed-scope", "retainer")
ITEM_FIELDS = {
    "key", "title", "organisation", "skills", "engagement_type", "country_tag", "work_mode", "summary",
}
SKILL_MAX = 40
SUMMARY_MAX = 500
# Every lead saved from the catalog gets this source channel; the item's
# organisation becomes the lead's source label.
SOURCE_CHANNEL = "platform"


class CatalogError(Exception):
    """The catalog file could not be read or failed validation."""


def unavailable():
    return ApiError(
        503,
        "CATALOG_UNAVAILABLE",
        "The fictional catalog could not be loaded. You can still add leads manually.",
    )


def _text(value, max_len):
    return isinstance(value, str) and value == value.strip() and 1 <= len(value) <= max_len


def parse_catalog(document):
    """Validate the decoded JSON document. Returns a tuple of item dicts."""
    if not isinstance(document, dict) or set(document) != {"format", "items"}:
        raise CatalogError("unexpected top-level structure")
    if document["format"] != CATALOG_FORMAT or not isinstance(document["items"], list):
        raise CatalogError("unsupported catalog format")
    items, keys = [], set()
    for index, item in enumerate(document["items"]):
        where = f"item {index}"
        if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
            raise CatalogError(f"{where}: unexpected fields")
        key = item["key"]
        if not (isinstance(key, str) and len(key) <= KEY_MAX and KEY_PATTERN.fullmatch(key)):
            raise CatalogError(f"{where}: invalid key")
        if key in keys:
            raise CatalogError(f"{where}: duplicate key")
        keys.add(key)
        if not _text(item["title"], validation.LABEL_MAX) or not _text(item["organisation"], validation.LABEL_MAX):
            raise CatalogError(f"{where}: invalid title or organisation")
        skills = item["skills"]
        if (not isinstance(skills, list) or not skills or len(set(skills)) != len(skills)
                or not all(_text(skill, SKILL_MAX) for skill in skills)):
            raise CatalogError(f"{where}: invalid skills")
        if item["engagement_type"] not in ENGAGEMENT_TYPES:
            raise CatalogError(f"{where}: invalid engagement type")
        if item["country_tag"] not in validation.COUNTRY_TAGS or item["work_mode"] not in validation.WORK_MODES:
            raise CatalogError(f"{where}: invalid country or work mode")
        if not _text(item["summary"], SUMMARY_MAX):
            raise CatalogError(f"{where}: invalid summary")
        items.append(dict(item, skills=list(skills)))
    return tuple(items)


class Catalog:
    """Loads the catalog file on first use and keeps it. A failed load is not
    cached, so fixing the file and reloading the page is enough."""

    def __init__(self, path=DEFAULT_CATALOG_PATH):
        self.path = Path(path)
        self._items = None
        self._lock = threading.Lock()

    def items(self):
        """All items, or raise ApiError 503 CATALOG_UNAVAILABLE."""
        with self._lock:
            if self._items is None:
                try:
                    document = json.loads(self.path.read_text(encoding="utf-8"))
                    self._items = parse_catalog(document)
                except (OSError, ValueError, CatalogError):
                    raise unavailable() from None
            return self._items

    def get(self, key):
        """One item by key, or raise 503 (catalog unavailable) / 404 (unknown key)."""
        for item in self.items():
            if item["key"] == key:
                return item
        raise ApiError(404, "NOT_FOUND", "This catalog item does not exist.")

    def skills(self):
        return sorted({skill for item in self.items() for skill in item["skills"]}, key=str.casefold)


# --- browsing


_FILTERS = ("skill", "engagement", "country", "mode")


def parse_filters(query, catalog):
    """{name: [values]} from parse_qs -> {name: value} for the filters that are set.

    Unknown or repeated parameters and values outside the catalog's
    vocabulary are 422 errors.
    """
    allowed = {
        "skill": tuple(catalog.skills()),
        "engagement": ENGAGEMENT_TYPES,
        "country": validation.COUNTRY_TAGS,
        "mode": validation.WORK_MODES,
    }
    problems = validation.Problems()
    clean = {}
    for name, values in query.items():
        if name not in _FILTERS:
            problems.add(name[:60], "Unexpected parameter.")
            continue
        if len(values) != 1:
            problems.add(name, "Give this filter once.")
            continue
        if values[0] != "":
            checked = validation.check_enum(values[0], name, problems, allowed[name])
            if checked is not None:
                clean[name] = checked
    problems.raise_if_any()
    return clean


def _matches(item, filters):
    return (
        ("skill" not in filters or filters["skill"] in item["skills"])
        and ("engagement" not in filters or item["engagement_type"] == filters["engagement"])
        and ("country" not in filters or item["country_tag"] == filters["country"])
        and ("mode" not in filters or item["work_mode"] == filters["mode"])
    )


def browse(conn, catalog, filters):
    """GET /api/v1/catalog payload. Read-only: it never creates or changes leads."""
    items = catalog.items()
    saved = {
        row["catalog_key"]: {"id": row["id"], "label": row["label"]}
        for row in conn.execute("SELECT id, label, catalog_key FROM lead WHERE catalog_key IS NOT NULL")
    }
    shown = [dict(item, saved_lead=saved.get(item["key"])) for item in items if _matches(item, filters)]
    return {
        "facets": {
            "skills": catalog.skills(),
            "engagement_types": list(ENGAGEMENT_TYPES),
            "country_tags": list(validation.COUNTRY_TAGS),
            "work_modes": list(validation.WORK_MODES),
        },
        "catalog_total": len(items),
        "total": len(shown),
        "items": shown,
    }


def lead_body(item):
    """The create-lead payload that a catalog item stands for. It goes through
    the same validation as a manually entered lead."""
    return {
        "label": item["title"],
        "country_tag": item["country_tag"],
        "work_mode": item["work_mode"],
        "source_channel": SOURCE_CHANNEL,
        "source_label": item["organisation"],
    }
