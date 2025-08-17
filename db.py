import sqlite3
from datetime import datetime
from db_schema import init_db
from models import ThreadRecord, Observation, ChecklistModeration, MissedObservation

DB_FILE = "dipper_bot.db"
_conn = None  # persistent connection


def get_connection():
    """Return a persistent SQLite connection, initializing tables if needed."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_FILE, detect_types=sqlite3.PARSE_DECLTYPES)
        _conn.row_factory = sqlite3.Row
        init_db(_conn)
    return _conn


# --------------------
# Thread Functions
# --------------------
def save_thread(thread: ThreadRecord):
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO threads (tracker_key, thread_id, type, last_seen_at, status_bucket)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tracker_key) DO UPDATE SET
                thread_id=excluded.thread_id,
                type=excluded.type,
                last_seen_at=excluded.last_seen_at,
                status_bucket=excluded.status_bucket
        """, (thread.tracker_key, thread.thread_id, thread.type,
              thread.last_seen_at.isoformat(), thread.status_bucket))


def get_thread(tracker_key: str) -> ThreadRecord | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM threads WHERE tracker_key=?", (tracker_key,)).fetchone()
    return row_to_thread(row) if row else None


def row_to_thread(row) -> ThreadRecord:
    return ThreadRecord(
        tracker_key=row["tracker_key"],
        thread_id=row["thread_id"],
        type=row["type"],
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        status_bucket=row["status_bucket"]
    )


# --------------------
# Checklist Functions
# --------------------
def save_checklist(obs: Observation):
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO checklists (checklist_id, species, region, observer, obs_datetime, thread_tracker_key)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(checklist_id) DO UPDATE SET
                species=excluded.species,
                region=excluded.region,
                observer=excluded.observer,
                obs_datetime=excluded.obs_datetime,
                thread_tracker_key=excluded.thread_tracker_key
        """, (obs.checklist_id, obs.species, obs.region, obs.observer,
              obs.obs_datetime.isoformat(), obs.thread_tracker_key))


def get_checklists_for_thread(tracker_key: str) -> list[Observation]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM checklists WHERE thread_tracker_key=?", (tracker_key,)).fetchall()
    return [row_to_observation(r) for r in rows]


def row_to_observation(row) -> Observation:
    return Observation(
        checklist_id=row["checklist_id"],
        species=row["species"],
        region=row["region"],
        observer=row["observer"],
        obs_datetime=datetime.fromisoformat(row["obs_datetime"]),
        thread_tracker_key=row["thread_tracker_key"]
    )


# --------------------
# Moderation Queue Functions
# --------------------
def save_pending_checklist(mod: ChecklistModeration):
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO moderation_queue (checklist_id, species, region, submitted_by, submitted_at, status, moderated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checklist_id) DO UPDATE SET
                species=excluded.species,
                region=excluded.region,
                submitted_by=excluded.submitted_by,
                submitted_at=excluded.submitted_at,
                status=excluded.status,
                moderated_by=excluded.moderated_by
        """, (mod.checklist_id, mod.species, mod.region, mod.submitted_by,
              mod.submitted_at.isoformat(), mod.status, mod.moderated_by))


def get_pending_moderation() -> list[ChecklistModeration]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM moderation_queue WHERE status='pending'").fetchall()
    return [row_to_moderation(r) for r in rows]


def update_moderation_status(checklist_id: str, status: str, moderated_by: str):
    conn = get_connection()
    with conn:
        conn.execute("""
            UPDATE moderation_queue
            SET status=?, moderated_by=?
            WHERE checklist_id=?
        """, (status, moderated_by, checklist_id))


def row_to_moderation(row) -> ChecklistModeration:
    return ChecklistModeration(
        checklist_id=row["checklist_id"],
        species=row["species"],
        region=row["region"],
        submitted_by=row["submitted_by"],
        submitted_at=datetime.fromisoformat(row["submitted_at"]),
        status=row["status"],
        moderated_by=row["moderated_by"]
    )


# --------------------
# Missed Observations Functions
# --------------------
def save_missed(missed: MissedObservation):
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO misses (observer, region, species, missed_at, thread_tracker_key)
            VALUES (?, ?, ?, ?, ?)
        """, (missed.observer, missed.region, missed.species,
              missed.missed_at.isoformat(), missed.thread_tracker_key))


def get_missed_for_thread(tracker_key: str) -> list[MissedObservation]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM misses WHERE thread_tracker_key=?", (tracker_key,)).fetchall()
    return [row_to_missed(r) for r in rows]


def row_to_missed(row) -> MissedObservation:
    return MissedObservation(
        observer=row["observer"],
        region=row["region"],
        species=row["species"],
        missed_at=datetime.fromisoformat(row["missed_at"]),
        thread_tracker_key=row["thread_tracker_key"]
    )
