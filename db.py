# db.py
import sqlite3
from datetime import datetime
from db_schema import init_db
from models import ThreadRecord, Observation, ChecklistModeration, MissedObservation, ModerationStatus, ModerationEntry
from time_utils import ebird_local_to_utc
from typing import Optional, List, Tuple
from dotenv import load_dotenv
import os

load_dotenv()
DB_FILE = os.getenv("DB_FILE")
DB_PATH =  f"./data/{DB_FILE}"
_conn = None  # persistent connection


def get_connection():
    """Return a persistent SQLite connection, initializing tables if needed."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
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
def save_checklist(obs: Observation, lat: float | None = None, lon: float | None = None):
    """Save checklist and convert to UTC if lat/lon provided."""
    if lat is not None and lon is not None:
        obs.obs_datetime = ebird_local_to_utc(obs.obs_datetime.strftime("%Y-%m-%d %H:%M"), lat, lon)
    
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO checklists (
                checklist_id, species, subspecies, region, observer, obs_datetime, local_tz,
                location, lat, lon, thread_tracker_key, counted, has_media
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checklist_id) DO UPDATE SET
                species=excluded.species,
                subspecies=excluded.subspecies,
                region=excluded.region,
                observer=excluded.observer,
                obs_datetime=excluded.obs_datetime,
                local_tz=excluded.local_tz,
                location=excluded.location,
                lat=excluded.lat,
                lon=excluded.lon,
                thread_tracker_key=excluded.thread_tracker_key,
                counted=excluded.counted,
                has_media=excluded.has_media
        """, (
            obs.checklist_id,
            obs.species,
            obs.subspecies or '',  # Use empty string if subspecies is None
            obs.region,
            obs.observer,
            obs.obs_datetime.isoformat(),
            obs.local_tz,
            obs.location,
            obs.lat,
            obs.lon,
            obs.thread_tracker_key,
            obs.counted,
            obs.has_media
        ))


def get_checklists_for_thread(tracker_key: str) -> list[Observation]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT checklist_id, species, subspecies, region, observer, obs_datetime, local_tz,
               location, lat, lon, thread_tracker_key, counted, has_media
        FROM checklists
        WHERE thread_tracker_key = ?
    """, (tracker_key,)).fetchall()
    
    return [row_to_observation(r) for r in rows]

def row_to_observation(row) -> Observation:
    return Observation(
        checklist_id=row["checklist_id"],
        species=row["species"],
        subspecies=row["subspecies"] or None,  # Convert empty string to None
        region=row["region"],
        observer=row["observer"],
        obs_datetime=datetime.fromisoformat(row["obs_datetime"]),
        local_tz=row["local_tz"],
        location=row["location"],
        lat=row["lat"],
        lon=row["lon"],
        thread_tracker_key=row["thread_tracker_key"],
        counted=row["counted"],
        has_media=row["has_media"]
    )

def add_subspecies_column_if_missing():
    conn = get_connection()
    conn.execute("""
        PRAGMA foreign_keys=off;
        ALTER TABLE checklists ADD COLUMN subspecies TEXT;
        PRAGMA foreign_keys=on;
    """)
    conn.commit()


# --------------------
# Enhanced Moderation Queue Functions
# --------------------
def save_moderation_entry(entry: ModerationEntry) -> int:
    """Save a moderation entry to database. Returns the entry ID."""
    conn = get_connection()
    with conn:
        cursor = conn.execute("""
            INSERT INTO moderation_queue (
                checklist_id, species, region, observer, location, obs_datetime, 
                local_tz, lat, lon, has_media, submitted_at, status, 
                moderated_by, moderated_at, discord_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checklist_id, species) DO UPDATE SET
                observer=excluded.observer,
                location=excluded.location,
                obs_datetime=excluded.obs_datetime,
                local_tz=excluded.local_tz,
                lat=excluded.lat,
                lon=excluded.lon,
                has_media=excluded.has_media,
                submitted_at=excluded.submitted_at
        """, (
            entry.checklist_id, entry.species, entry.region, entry.observer,
            entry.location, entry.obs_datetime.isoformat(), entry.local_tz,
            entry.lat, entry.lon, entry.has_media, entry.submitted_at.isoformat(),
            entry.status.value, entry.moderated_by, 
            entry.moderated_at.isoformat() if entry.moderated_at else None,
            entry.discord_message_id
        ))
        return cursor.lastrowid


def get_moderation_entry_by_composite(checklist_id: str, species: str) -> Optional[ModerationEntry]:
    """Get a moderation entry by checklist_id and species."""
    conn = get_connection()
    row = conn.execute("""
        SELECT id, checklist_id, species, region, observer, location, obs_datetime,
               local_tz, lat, lon, has_media, submitted_at, status, moderated_by, 
               moderated_at, discord_message_id
        FROM moderation_queue 
        WHERE checklist_id = ? AND species = ?
    """, (checklist_id, species)).fetchone()
    
    if row:
        return ModerationEntry(
            id=row["id"],
            checklist_id=row["checklist_id"],
            species=row["species"],
            region=row["region"],
            observer=row["observer"],
            location=row["location"],
            obs_datetime=datetime.fromisoformat(row["obs_datetime"]),
            local_tz=row["local_tz"],
            lat=row["lat"],
            lon=row["lon"],
            has_media=row["has_media"],
            submitted_at=datetime.fromisoformat(row["submitted_at"]),
            status=ModerationStatus(row["status"]),
            moderated_by=row["moderated_by"],
            moderated_at=datetime.fromisoformat(row["moderated_at"]) if row["moderated_at"] else None,
            discord_message_id=row["discord_message_id"]
        )
    return None


def get_pending_moderation_entries() -> List[ModerationEntry]:
    """Get all pending moderation entries."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, checklist_id, species, region, observer, location, obs_datetime,
               local_tz, lat, lon, has_media, submitted_at, status, moderated_by, 
               moderated_at, discord_message_id
        FROM moderation_queue 
        WHERE status = 'pending'
        ORDER BY submitted_at ASC
    """).fetchall()
    
    entries = []
    for row in rows:
        entries.append(ModerationEntry(
            id=row["id"],
            checklist_id=row["checklist_id"],
            species=row["species"],
            region=row["region"],
            observer=row["observer"],
            location=row["location"],
            obs_datetime=datetime.fromisoformat(row["obs_datetime"]),
            local_tz=row["local_tz"],
            lat=row["lat"],
            lon=row["lon"],
            has_media=row["has_media"],
            submitted_at=datetime.fromisoformat(row["submitted_at"]),
            status=ModerationStatus(row["status"]),
            moderated_by=row["moderated_by"],
            moderated_at=datetime.fromisoformat(row["moderated_at"]) if row["moderated_at"] else None,
            discord_message_id=row["discord_message_id"]
        ))
    
    return entries


def update_moderation_entry_status_by_composite(
    checklist_id: str, 
    species: str, 
    new_status: ModerationStatus, 
    moderated_by: str,
    moderated_at: Optional[datetime] = None,
    discord_message_id: Optional[str] = None
) -> bool:
    """Update the status of a moderation entry by checklist_id and species."""
    if moderated_at is None:
        moderated_at = datetime.now()
    
    conn = get_connection()
    with conn:
        cursor = conn.execute("""
            UPDATE moderation_queue 
            SET status = ?, moderated_by = ?, moderated_at = ?, discord_message_id = ?
            WHERE checklist_id = ? AND species = ?
        """, (
            new_status.value, moderated_by, moderated_at.isoformat(),
            discord_message_id, checklist_id, species
        ))
        return cursor.rowcount > 0


def update_discord_message_id_by_composite(checklist_id: str, species: str, message_id: str) -> bool:
    """Update the Discord message ID for a moderation entry."""
    conn = get_connection()
    with conn:
        cursor = conn.execute("""
            UPDATE moderation_queue 
            SET discord_message_id = ?
            WHERE checklist_id = ? AND species = ?
        """, (message_id, checklist_id, species))
        return cursor.rowcount > 0


def is_already_processed_by_composite(checklist_id: str, species: str) -> bool:
    """Check if this checklist+species combo has already been processed."""
    conn = get_connection()
    result = conn.execute("""
        SELECT status FROM moderation_queue 
        WHERE checklist_id = ? AND species = ?
    """, (checklist_id, species)).fetchone()
    
    if result:
        status = result["status"]
        return status in ['accepted', 'rejected']
    return False


# --------------------
# Legacy Moderation Queue Functions (for backward compatibility)
# --------------------
def save_pending_checklist(mod: ChecklistModeration):
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO moderation_queue
                (checklist_id, species, region, submitted_by, submitted_at, status, moderated_by, moderated_at, merge_target_thread)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checklist_id, species) DO UPDATE SET
                region=excluded.region,
                status=excluded.status,
                moderated_by=excluded.moderated_by,
                moderated_at=excluded.moderated_at
        """, (
            mod.checklist_id,
            mod.species,
            mod.region,
            mod.submitted_by,
            mod.submitted_at.isoformat(),
            mod.status.value if isinstance(mod.status, ModerationStatus) else mod.status,
            mod.moderated_by,
            mod.moderated_at.isoformat() if mod.moderated_at else None,
            mod.merge_target_thread
        ))

def get_moderation_entry(checklist_id: str) -> ChecklistModeration | None:
    """Legacy function - gets first moderation entry for a checklist."""
    conn = get_connection()
    row = conn.execute("""
        SELECT checklist_id, species, region, observer, submitted_at, status,
               moderated_by, moderated_at
        FROM moderation_queue
        WHERE checklist_id = ?
        LIMIT 1
    """, (checklist_id,)).fetchone()

    if row:
        return ChecklistModeration(
            checklist_id=row["checklist_id"],
            species=row["species"],
            region=row["region"],
            submitted_by=row["observer"],
            submitted_at=datetime.fromisoformat(row["submitted_at"]),
            status=ModerationStatus(row["status"]),
            moderated_by=row["moderated_by"],
            moderated_at=datetime.fromisoformat(row["moderated_at"]) if row["moderated_at"] else None,
            merge_target_thread=None
        )
    return None

def update_moderation_status(checklist_id, status, moderated_by=None, moderated_at=None, conn=None):
    """Legacy function for backward compatibility."""
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True

    with conn:
        conn.execute("""
            UPDATE moderation_queue
            SET status = ?,
                moderated_by = ?,
                moderated_at = ?
            WHERE checklist_id = ?
        """, (status, moderated_by, moderated_at.isoformat() if moderated_at else None, checklist_id))

    if own_conn:
        conn.close()


def get_pending_moderation() -> list[ChecklistModeration]:
    """Legacy function - returns ChecklistModeration objects for backward compatibility."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM moderation_queue WHERE status='pending'").fetchall()
    results = []
    for row in rows:
        results.append(ChecklistModeration(
            checklist_id=row["checklist_id"],
            species=row["species"],
            region=row["region"],
            submitted_by=row["observer"],
            submitted_at=datetime.fromisoformat(row["submitted_at"]),
            status=ModerationStatus(row["status"]),
            moderated_by=row["moderated_by"],
            moderated_at=datetime.fromisoformat(row["moderated_at"]) if row["moderated_at"] else None,
            merge_target_thread=None
        ))
    return results

def row_to_moderation(row) -> ChecklistModeration:
    """Legacy function for backward compatibility."""
    return ChecklistModeration(
        checklist_id=row["checklist_id"],
        species=row["species"],
        region=row["region"],
        submitted_by=row["observer"],
        submitted_at=datetime.fromisoformat(row["submitted_at"]),
        status=ModerationStatus(row["status"]),
        moderated_by=row["moderated_by"],
        moderated_at=datetime.fromisoformat(row["moderated_at"]) if row["moderated_at"] else None,
        merge_target_thread=None
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

# --------------------
# State Review Functions
# --------------------
def save_review_species(species_name: str, review_flag: bool):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO state_review_species (species_name, review_flag) VALUES (?, ?)",
            (species_name, int(review_flag))
        )

def load_review_species() -> List[Tuple[str, bool]]:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT species_name, review_flag FROM state_review_species")
        return [(row[0], bool(row[1])) for row in cursor.fetchall()]

def is_species_flagged(species_name: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT review_flag FROM state_review_species WHERE species_name = ?",
            (species_name,)
        )
        row = cursor.fetchone()
        return bool(row[0]) if row else False

# --------------------
# Utilities
# --------------------
def get_all_county_regions():
    """
    Returns a list of dicts: [{"code": "US-CO-013", "name": "El Paso"}, ...]
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT code, name FROM regions WHERE code LIKE 'US-CO-%'")
    rows = cur.fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1]} for r in rows]


def close_connection():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def get_checklist(checklist_id: str) -> Observation | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM checklists WHERE checklist_id=?", (checklist_id,)).fetchone()
    return row_to_observation(row) if row else None


def get_all_threads() -> list[ThreadRecord]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM threads").fetchall()
    return [row_to_thread(r) for r in rows]


def delete_thread(tracker_key: str):
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM threads WHERE tracker_key=?", (tracker_key,))