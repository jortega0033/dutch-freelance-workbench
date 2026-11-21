"""Read-only queries: dashboard, lead list and lead detail.

Buckets are computed here from the clock's business date at read time.
Nothing about buckets is ever stored, so crossing Amsterdam midnight
reclassifies leads without any write.
"""

from . import dates

LEAD_COLUMNS = (
    "id, label, country_tag, work_mode, source_channel, source_label, stage, version,"
    " next_action_text, next_action_due, created_at_utc, updated_at_utc, catalog_key"
)
BUCKET_ORDER = ("overdue", "today", "upcoming", "no_action")


def bucket_for(due_date, today):
    """due_date and today are 'YYYY-MM-DD' strings (or None for no action)."""
    if due_date is None:
        return "no_action"
    if due_date < today:
        return "overdue"
    if due_date == today:
        return "today"
    return "upcoming"


def _next_action(row):
    if row["next_action_text"] is None:
        return None
    return {"text": row["next_action_text"], "due_date": row["next_action_due"]}


def lead_dict(row, today):
    """LeadSummary / LeadDetail shape."""
    return {
        "id": row["id"],
        "label": row["label"],
        "country_tag": row["country_tag"],
        "work_mode": row["work_mode"],
        "source_channel": row["source_channel"],
        "source_label": row["source_label"],
        "stage": row["stage"],
        "version": row["version"],
        "next_action": _next_action(row),
        "bucket": bucket_for(row["next_action_due"], today),
        "created_at_utc": row["created_at_utc"],
        "updated_at_utc": row["updated_at_utc"],
        "catalog_key": row["catalog_key"],
    }


def row_dict(row, today):
    """Dashboard Row shape."""
    return {
        "id": row["id"],
        "label": row["label"],
        "stage": row["stage"],
        "next_action": _next_action(row),
        "bucket": bucket_for(row["next_action_due"], today),
    }


def activity_dict(row):
    return {
        "id": row["id"],
        "kind": row["kind"],
        "business_date": row["business_date"],
        "summary": row["summary"],
        "recorded_at_utc": row["recorded_at_utc"],
    }


def has_profile(conn):
    return conn.execute("SELECT 1 FROM profile WHERE id = 1").fetchone() is not None


def profile_dict(conn):
    row = conn.execute("SELECT display_label, version FROM profile WHERE id = 1").fetchone()
    if row is None:
        return None
    return {
        "display_label": row["display_label"],
        "version": row["version"],
        "timezone": dates.TIMEZONE_NAME,
        "language": "en",
    }


def lead_detail(conn, lead_id, today):
    """LeadDetail dict, or None when the lead does not exist."""
    row = conn.execute(f"SELECT {LEAD_COLUMNS} FROM lead WHERE id = ?", (lead_id,)).fetchone()
    return None if row is None else lead_dict(row, today)


def lead_activities(conn, lead_id):
    rows = conn.execute(
        "SELECT id, kind, business_date, summary, recorded_at_utc FROM activity"
        " WHERE lead_id = ? ORDER BY id ASC",
        (lead_id,),
    ).fetchall()
    return [activity_dict(row) for row in rows]


def list_leads(conn, today, filters):
    """`filters` comes from validation.parse_lead_filters. Sorted by id ascending."""
    where, params = [], []
    if "q" in filters:
        needle = filters["q"].casefold()
        where.append(
            "(instr(casefold(label), ?) > 0 OR instr(casefold(COALESCE(source_label, '')), ?) > 0)"
        )
        params += [needle, needle]
    for name, column in (
        ("stage", "stage"),
        ("country", "country_tag"),
        ("mode", "work_mode"),
        ("source", "source_channel"),
    ):
        if name in filters:
            where.append(f"{column} = ?")
            params.append(filters[name])
    sql = f"SELECT {LEAD_COLUMNS} FROM lead"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id ASC"
    leads = [lead_dict(row, today) for row in conn.execute(sql, params).fetchall()]
    if "bucket" in filters:
        leads = [lead for lead in leads if lead["bucket"] == filters["bucket"]]
    return {"business_date": today, "total": len(leads), "leads": leads}


def dashboard(conn, today):
    rows = conn.execute(f"SELECT {LEAD_COLUMNS} FROM lead").fetchall()
    groups = {name: [] for name in BUCKET_ORDER}
    for row in rows:
        entry = row_dict(row, today)
        groups[entry["bucket"]].append(entry)
    for name in ("overdue", "today", "upcoming"):
        groups[name].sort(key=lambda r: (r["next_action"]["due_date"], r["label"].casefold(), r["id"]))
    groups["no_action"].sort(key=lambda r: (r["label"].casefold(), r["id"]))
    counts = {name: len(groups[name]) for name in BUCKET_ORDER}
    counts["total"] = len(rows)
    return {
        "business_date": today,
        "timezone": dates.TIMEZONE_NAME,
        "has_profile": has_profile(conn),
        "counts": counts,
        "groups": groups,
    }
