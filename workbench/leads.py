"""Write commands. One HTTP save = one call to run_mutation = one transaction.

Trace of a Save button:
  UI form -> api.js apiMutate() -> server.py route -> run_mutation() below
  -> <command>(conn, ctx, lead_id, body) -> db.write_transaction (BEGIN IMMEDIATE .. COMMIT)

run_mutation() inside the transaction does, in order:
  1. look up the Idempotency-Key (exact replay returns the stored response)
  2. run the command: validate -> load -> check version -> check rules -> write
     rows -> bump lead.version once -> insert the audit row
  3. store the idempotency row holding the exact response
  4. faults.hit("before_commit"), then COMMIT
Any exception rolls everything back, so a failed save leaves no trace.
"""

import json
from dataclasses import dataclass

from . import dates, db, queries, validation
from .errors import ApiError


@dataclass
class MutationResult:
    status: int
    body_text: str  # exact JSON text sent to the client (and stored for replays)
    replayed: bool


class _Replay(Exception):
    """Internal: unwinds the transaction (ROLLBACK) to return a stored response."""

    def __init__(self, status, body_text):
        super().__init__("replay")
        self.status = status
        self.body_text = body_text


def run_mutation(ctx, method, target, key, body, command, lead_id=None):
    """Execute `command` atomically with idempotency handling. See module docstring."""
    rhash = db.request_hash(body)
    conn = ctx.connect()
    try:
        try:
            with db.write_transaction(conn):
                stored = db.find_idempotent(conn, key)
                if stored is not None:
                    if (stored["method"], stored["target"], stored["request_hash"]) != (method, target, rhash):
                        raise ApiError(
                            409,
                            "KEY_REUSED",
                            "This Idempotency-Key was already used for a different request.",
                        )
                    raise _Replay(stored["status_code"], stored["response_json"])
                status, payload = command(conn, ctx, lead_id, body)
                body_text = json.dumps(payload, ensure_ascii=False)
                stamp = dates.utc_stamp(ctx.clock.now_utc())
                db.store_idempotent(conn, key, method, target, rhash, status, body_text, stamp)
                ctx.faults.hit("before_commit")
        except _Replay as replay:
            return MutationResult(replay.status, replay.body_text, True)
        return MutationResult(status, body_text, False)
    finally:
        conn.close()


# --- helpers shared by commands


def _now(ctx):
    """(utc timestamp string, Amsterdam business date as date)"""
    moment = ctx.clock.now_utc()
    return dates.utc_stamp(moment), dates.business_date(moment)


def _stale(current_version, reload_path):
    return ApiError(
        409,
        "STALE_VERSION",
        "This record changed since you loaded it. Reload to see the current version.",
        extra={"current_version": current_version, "reload": reload_path},
    )


def _load_lead(conn, lead_id, expected_version):
    row = conn.execute("SELECT * FROM lead WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Lead not found.")
    if row["version"] != expected_version:
        raise _stale(row["version"], f"/api/v1/leads/{lead_id}")
    return row


_UPDATABLE = {
    "label", "country_tag", "work_mode", "source_channel", "source_label",
    "stage", "next_action_text", "next_action_due",
}


def _update_lead(conn, lead_id, stamp, changes):
    """Apply `changes` and bump lead.version exactly once. Returns the new version."""
    assert set(changes) <= _UPDATABLE  # column names never come from the client
    assignments = "".join(f"{column} = ?, " for column in changes)
    conn.execute(
        f"UPDATE lead SET {assignments}version = version + 1, updated_at_utc = ? WHERE id = ?",
        [*changes.values(), stamp, lead_id],
    )
    return conn.execute("SELECT version FROM lead WHERE id = ?", (lead_id,)).fetchone()["version"]


def _action_columns(action):
    if action is None:
        return {"next_action_text": None, "next_action_due": None}
    return {"next_action_text": action["text"], "next_action_due": action["due_date"]}


def _lead_response(conn, lead_id, today):
    return queries.lead_detail(conn, lead_id, today.isoformat())


# --- commands: each returns (http_status, response_payload)


def save_profile(conn, ctx, lead_id, body):
    version, label = validation.parse_profile(body)
    stamp, _ = _now(ctx)
    row = conn.execute("SELECT version FROM profile WHERE id = 1").fetchone()
    current = 0 if row is None else row["version"]
    if version != current:
        raise _stale(current, "/api/v1/profile")
    if row is None:
        conn.execute(
            "INSERT INTO profile (id, display_label, version, created_at_utc, updated_at_utc)"
            " VALUES (1, ?, 1, ?, ?)",
            (label, stamp, stamp),
        )
    else:
        conn.execute(
            "UPDATE profile SET display_label = ?, version = version + 1, updated_at_utc = ? WHERE id = 1",
            (label, stamp),
        )
    db.insert_audit(conn, "profile.save", "profile", 1, current + 1, stamp)
    return 200, {"profile": queries.profile_dict(conn)}


def create_lead(conn, ctx, lead_id, body):
    clean = validation.parse_lead_create(body)
    stamp, today = _now(ctx)
    if not queries.has_profile(conn):
        raise ApiError(409, "PROFILE_REQUIRED", "Set up your profile in Settings before adding leads.")
    cursor = conn.execute(
        "INSERT INTO lead (label, country_tag, work_mode, source_channel, source_label, stage, version,"
        " created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, 'new', 1, ?, ?)",
        (clean["label"], clean["country_tag"], clean["work_mode"], clean["source_channel"],
         clean["source_label"], stamp, stamp),
    )
    new_id = cursor.lastrowid
    db.insert_audit(conn, "lead.create", "lead", new_id, 1, stamp, None, "new")
    return 201, {"lead": _lead_response(conn, new_id, today)}


def update_lead(conn, ctx, lead_id, body):
    version, changes = validation.parse_lead_patch(body)
    stamp, today = _now(ctx)
    _load_lead(conn, lead_id, version)
    new_version = _update_lead(conn, lead_id, stamp, changes)
    db.insert_audit(conn, "lead.update", "lead", lead_id, new_version, stamp)
    return 200, {"lead": _lead_response(conn, lead_id, today)}


def add_activity(conn, ctx, lead_id, body):
    stamp, today = _now(ctx)
    clean = validation.parse_activity(body, today)
    lead = _load_lead(conn, lead_id, clean["version"])

    changes = {}
    from_stage = to_stage = None
    if clean["kind"] == "outreach" and lead["stage"] != "contacted":
        # first outreach: the lead becomes contacted and must have a follow-up
        if clean["next_action"] is None:
            raise ApiError(
                422,
                "NEXT_ACTION_REQUIRED",
                "Add a follow-up action and due date to record the first outreach.",
                fields={"next_action": "Required."},
            )
        changes["stage"] = "contacted"
        from_stage, to_stage = lead["stage"], "contacted"
    if clean["next_action"] is not None:
        changes.update(_action_columns(clean["next_action"]))

    cursor = conn.execute(
        "INSERT INTO activity (lead_id, kind, business_date, summary, recorded_at_utc)"
        " VALUES (?, ?, ?, ?, ?)",
        (lead_id, clean["kind"], clean["business_date"], clean["summary"], stamp),
    )
    activity_id = cursor.lastrowid
    ctx.faults.hit("activity.after_insert")

    new_version = _update_lead(conn, lead_id, stamp, changes)
    db.insert_audit(conn, f"activity.{clean['kind']}", "lead", lead_id, new_version, stamp,
                    from_stage, to_stage)
    activity = conn.execute(
        "SELECT id, kind, business_date, summary, recorded_at_utc FROM activity WHERE id = ?",
        (activity_id,),
    ).fetchone()
    return 201, {
        "lead": _lead_response(conn, lead_id, today),
        "activity": queries.activity_dict(activity),
    }


def set_next_action(conn, ctx, lead_id, body):
    version, action = validation.parse_next_action_put(body)
    stamp, today = _now(ctx)
    lead = _load_lead(conn, lead_id, version)
    if action is None and lead["stage"] != "new":
        raise ApiError(
            422,
            "NEXT_ACTION_REQUIRED",
            "A lead that is being researched or contacted needs a follow-up action.",
            fields={"next_action": "Required."},
        )
    new_version = _update_lead(conn, lead_id, stamp, _action_columns(action))
    db.insert_audit(conn, "lead.next_action.set", "lead", lead_id, new_version, stamp)
    return 200, {"lead": _lead_response(conn, lead_id, today)}


def change_stage(conn, ctx, lead_id, body):
    version, stage, action = validation.parse_stage(body)
    stamp, today = _now(ctx)
    lead = _load_lead(conn, lead_id, version)
    if stage == "contacted":
        raise ApiError(
            422, "INVALID_TRANSITION", "Record an outreach activity to mark a lead as contacted."
        )
    if not (lead["stage"] == "new" and stage == "researching"):
        raise ApiError(
            422, "INVALID_TRANSITION", f"A lead cannot move from {lead['stage']} to {stage}."
        )
    has_action = action is not None or lead["next_action_text"] is not None
    if not has_action:
        raise ApiError(
            422,
            "NEXT_ACTION_REQUIRED",
            "Add a follow-up action and due date before researching a lead.",
            fields={"next_action": "Required."},
        )
    changes = {"stage": "researching"}
    if action is not None:
        changes.update(_action_columns(action))
    new_version = _update_lead(conn, lead_id, stamp, changes)
    db.insert_audit(conn, "lead.stage.change", "lead", lead_id, new_version, stamp, "new", "researching")
    return 200, {"lead": _lead_response(conn, lead_id, today)}
