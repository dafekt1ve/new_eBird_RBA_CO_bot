# db.py - Fixed with correct paths and missing functions
import sqlite3
from datetime import datetime, timezone
from db_schema import init_db
from models import ThreadRecord, Observation, ChecklistModeration, MissedObservation, ModerationStatus
from time_utils import ebird_local_to_utc
from geo_utils import haversine, normalize_species_name
import logging
from dotenv import load_dotenv
import os
from typing import List, Dict

logger = logging.getLogger("Dipper_RBA_Bot")

load_dotenv()
DB_FILE = os.getenv("DB_FILE", "dipper_bot.db")
DB_PATH = f"./data/{DB_FILE}"
_conn = None  # persistent connection

def get_connection():
    """Return a persistent SQLite connection, initializing tables if needed."""
    global _conn
    if _conn is None:
        # Create data directory if it doesn't exist
        os.makedirs("./data", exist_ok=True)
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
            INSERT INTO threads (tracker_key, thread_id, discord_channel_id, discord_message_id, 
                               type, last_seen_at, status_bucket)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracker_key) DO UPDATE SET
                thread_id=excluded.thread_id,
                discord_channel_id=excluded.discord_channel_id,
                discord_message_id=excluded.discord_message_id,
                type=excluded.type,
                last_seen_at=excluded.last_seen_at,
                status_bucket=excluded.status_bucket
        """, (thread.tracker_key, thread.thread_id, 
              getattr(thread, 'discord_channel_id', None),
              getattr(thread, 'discord_message_id', None),
              thread.type, thread.last_seen_at.isoformat(), thread.status_bucket))

def get_thread(tracker_key: str) -> ThreadRecord | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM threads WHERE tracker_key=?", (tracker_key,)).fetchone()
    return row_to_thread(row) if row else None

def row_to_thread(row) -> ThreadRecord:
    thread = ThreadRecord(
        tracker_key=row["tracker_key"],
        thread_id=row["thread_id"],
        type=row["type"],
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        status_bucket=row["status_bucket"]
    )
    # Add discord IDs if present
    thread.discord_channel_id = row.get("discord_channel_id")
    thread.discord_message_id = row.get("discord_message_id")
    return thread

def get_all_threads() -> list[ThreadRecord]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM threads").fetchall()
    return [row_to_thread(r) for r in rows]

def delete_thread(tracker_key: str):
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM threads WHERE tracker_key=?", (tracker_key,))

# --------------------
# Enhanced Checklist Functions
# --------------------
def save_checklist(obs: Observation, lat: float | None = None, lon: float | None = None):
    """Enhanced save with all new fields"""
    if lat is not None and lon is not None:
        obs.obs_datetime = ebird_local_to_utc(obs.obs_datetime.strftime("%Y-%m-%d %H:%M"), lat, lon)

    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO checklists (
                checklist_id, species, species_code, region, observer, obs_datetime, 
                thread_tracker_key, lat, lon, location, has_media, local_tz
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checklist_id) DO UPDATE SET
                species=excluded.species,
                species_code=excluded.species_code,
                region=excluded.region,
                observer=excluded.observer,
                obs_datetime=excluded.obs_datetime,
                thread_tracker_key=excluded.thread_tracker_key,
                lat=excluded.lat,
                lon=excluded.lon,
                location=excluded.location,
                has_media=excluded.has_media,
                local_tz=excluded.local_tz
        """, (
            obs.checklist_id, obs.species, getattr(obs, 'species_code', None), 
            obs.region, obs.observer, obs.obs_datetime.isoformat(), 
            obs.thread_tracker_key, obs.lat, obs.lon, obs.location, 
            obs.has_media, obs.local_tz
        ))

def get_checklists_for_thread(tracker_key: str) -> list[Observation]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM checklists WHERE thread_tracker_key=?", (tracker_key,)).fetchall()
    return [row_to_observation(r) for r in rows]

def get_checklist(checklist_id: str) -> Observation | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM checklists WHERE checklist_id=?", (checklist_id,)).fetchone()
    return row_to_observation(row) if row else None

def row_to_observation(row) -> Observation:
    return Observation(
        checklist_id=row["checklist_id"],
        species=row["species"],
        region=row["region"],
        location=row.get("location", "Unknown"),
        observer=row.get("observer", "Unknown"),
        obs_datetime=datetime.fromisoformat(row["obs_datetime"]),
        local_tz=row.get("local_tz", "UTC"),
        thread_tracker_key=row["thread_tracker_key"],
        lat=row.get("lat"),
        lon=row.get("lon"),
        has_media=bool(row.get("has_media", 0))
    )

def mark_checklist_processed_for_moderation(checklist_id: str):
    """Mark a checklist as having been processed for moderation to avoid reprocessing"""
    conn = get_connection()
    with conn:
        conn.execute("""
            UPDATE checklists 
            SET processed_for_moderation = 1 
            WHERE checklist_id = ?
        """, (checklist_id,))

def get_unprocessed_checklists_for_moderation() -> list[Observation]:
    """Get checklists that haven't been processed for moderation yet"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM checklists 
        WHERE processed_for_moderation = 0
        ORDER BY obs_datetime DESC
    """).fetchall()
    return [row_to_observation(r) for r in rows]

# --------------------
# Statewide Review List Functions (simplified for CO_Review_List.txt)
# --------------------
def get_species_from_review_list(species_name: str) -> dict | None:
    """Get species info from review list by common name"""
    conn = get_connection()
    row = conn.execute("""
        SELECT * FROM statewide_thresholds WHERE common_name = ? COLLATE NOCASE
    """, (species_name,)).fetchone()
    
    if row:
        return {
            'common_name': row['common_name'],
            'is_review_species': bool(row['is_review_species']),
            'in_review_list': True
        }
    return None

def is_species_flagged(species_name: str) -> bool:
    """Check if a species is flagged for moderation based on CO Review List"""
    try:
        from co_review_loader import is_species_statewide_rba
        return is_species_statewide_rba(species_name)
    except ImportError:
        # Fallback to database check
        conn = get_connection()
        row = conn.execute("""
            SELECT is_review_species FROM statewide_thresholds 
            WHERE common_name = ? COLLATE NOCASE
        """, (species_name,)).fetchone()
        
        if row:
            return bool(row['is_review_species'])
        # Not in list = flagged for moderation
        return True

# --------------------
# Enhanced Moderation Queue Functions
# --------------------
def save_pending_checklist(obs_or_moderation, discord_message_id: int | None = None) -> bool:
    """Save to moderation queue, accepts either Observation or ChecklistModeration"""
    conn = get_connection()
    
    # Handle both Observation and ChecklistModeration objects
    if isinstance(obs_or_moderation, Observation):
        obs = obs_or_moderation
        try:
            with conn:
                # ENHANCED: Include species_code and has_media
                conn.execute("""
                    INSERT INTO moderation_queue (
                        checklist_id, species, species_code, region, observer, 
                        location, lat, lon, obs_datetime, has_media, discord_message_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    obs.checklist_id, obs.species, getattr(obs, 'species_code', ''),
                    obs.region, obs.observer, obs.location, obs.lat, obs.lon, 
                    obs.obs_datetime.isoformat(), obs.has_media, discord_message_id
                ))
            return True
        except sqlite3.IntegrityError:
            logger.info(f"Duplicate moderation entry for {obs.checklist_id} - {obs.species}")
            return False
    
    elif isinstance(obs_or_moderation, ChecklistModeration):
        mod = obs_or_moderation
        try:
            with conn:
                conn.execute("""
                    INSERT INTO moderation_queue (
                        checklist_id, species, region, submitted_by, submitted_at, 
                        status, moderated_by, discord_message_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    mod.checklist_id, mod.species, mod.region, mod.submitted_by,
                    mod.submitted_at.isoformat(), mod.status.value, 
                    mod.moderated_by, discord_message_id
                ))
            return True
        except sqlite3.IntegrityError:
            logger.info(f"Duplicate moderation entry for {mod.checklist_id} - {mod.species}")
            return False
    
    else:
        logger.error(f"Invalid type for save_pending_checklist: {type(obs_or_moderation)}")
        return False

def get_pending_moderation() -> list[dict]:
    """Get all pending moderation items"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM moderation_queue WHERE status='pending' 
        ORDER BY submitted_at ASC
    """).fetchall()
    
    return [dict(row) for row in rows]

def update_moderation_status(checklist_id: str, status: str, moderated_by: str = None, 
                           moderated_at: datetime = None, species: str = None,
                           merge_target_thread: str = None, rejection_reason: str = None):
    """Update moderation status - flexible parameters"""
    conn = get_connection()
    
    if moderated_at is None:
        moderated_at = datetime.now(timezone.utc)
    
    with conn:
        if species:
            # Update specific species for checklist
            conn.execute("""
                UPDATE moderation_queue
                SET status=?, moderated_by=?, moderated_at=?, merge_target_thread=?, rejection_reason=?
                WHERE checklist_id=? AND species=?
            """, (status, moderated_by, moderated_at.isoformat(), 
                  merge_target_thread, rejection_reason, checklist_id, species))
        else:
            # Update all entries for checklist
            conn.execute("""
                UPDATE moderation_queue
                SET status=?, moderated_by=?, moderated_at=?, merge_target_thread=?, rejection_reason=?
                WHERE checklist_id=?
            """, (status, moderated_by, moderated_at.isoformat(), 
                  merge_target_thread, rejection_reason, checklist_id))
        
        # If rejected, also add to rejected_checklists table
        if status == 'rejected':
            try:
                if species:
                    conn.execute("""
                        INSERT INTO rejected_checklists (checklist_id, species, rejected_by)
                        VALUES (?, ?, ?)
                    """, (checklist_id, species, moderated_by))
                else:
                    # Get all species for this checklist and reject them
                    species_rows = conn.execute("""
                        SELECT DISTINCT species FROM moderation_queue WHERE checklist_id = ?
                    """, (checklist_id,)).fetchall()
                    
                    for species_row in species_rows:
                        conn.execute("""
                            INSERT INTO rejected_checklists (checklist_id, species, rejected_by)
                            VALUES (?, ?, ?)
                        """, (checklist_id, species_row['species'], moderated_by))
            except sqlite3.IntegrityError:
                pass  # Already exists

def is_checklist_rejected(checklist_id: str, species: str) -> bool:
    """Check if this checklist+species combination was already rejected"""
    conn = get_connection()
    count = conn.execute("""
        SELECT COUNT(*) FROM rejected_checklists 
        WHERE checklist_id = ? AND species = ?
    """, (checklist_id, species)).fetchone()[0]
    return count > 0

def get_moderation_item_by_message_id(discord_message_id: int) -> dict | None:
    """Get moderation item by Discord message ID"""
    conn = get_connection()
    row = conn.execute("""
        SELECT * FROM moderation_queue WHERE discord_message_id = ?
    """, (discord_message_id,)).fetchone()
    return dict(row) if row else None

def get_moderation_entry(checklist_id: str) -> ChecklistModeration | None:
    """Get moderation entry by checklist ID"""
    conn = get_connection()
    row = conn.execute("""
        SELECT checklist_id, species, region, submitted_by, submitted_at, status,
               moderated_by, moderated_at, merge_target_thread
        FROM moderation_queue
        WHERE checklist_id = ?
        ORDER BY submitted_at DESC
        LIMIT 1
    """, (checklist_id,)).fetchone()

    if row:
        return ChecklistModeration(
            checklist_id=row["checklist_id"],
            species=row["species"],
            region=row["region"],
            submitted_by=row["submitted_by"],
            submitted_at=datetime.fromisoformat(row["submitted_at"]),
            status=ModerationStatus(row["status"]),
            moderated_by=row["moderated_by"],
            moderated_at=datetime.fromisoformat(row["moderated_at"]) if row["moderated_at"] else None,
            merge_target_thread=row["merge_target_thread"]
        )
    return None

# --------------------
# Thread Proximity Functions
# --------------------
def find_nearby_thread(species: str, lat: float, lon: float, distance_km: float = 4.0) -> str | None:
    """Find if there's already an accepted thread for this species within distance_km"""
    conn = get_connection()
    
    # Get all threads for this species that have been accepted
    rows = conn.execute("""
        SELECT DISTINCT t.tracker_key, c.lat, c.lon
        FROM threads t
        JOIN checklists c ON t.tracker_key = c.thread_tracker_key
        WHERE c.species = ? AND c.lat IS NOT NULL AND c.lon IS NOT NULL
    """, (species,)).fetchall()
    
    for row in rows:
        thread_lat, thread_lon = row['lat'], row['lon']
        if haversine(lat, lon, thread_lat, thread_lon) <= distance_km:
            return row['tracker_key']
    
    return None

# --------------------
# Thread Participants Functions
# --------------------
def add_thread_participant(thread_tracker_key: str, observer: str, checklist_id: str, obs_datetime: datetime):
    """Add or update thread participant"""
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO thread_participants (thread_tracker_key, observer, checklist_id, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(thread_tracker_key, observer) DO UPDATE SET
                checklist_id=excluded.checklist_id,
                last_seen_at=excluded.last_seen_at
        """, (thread_tracker_key, observer, checklist_id, 
              obs_datetime.isoformat(), obs_datetime.isoformat()))

def get_thread_participants(thread_tracker_key: str) -> list[dict]:
    """Get all participants for a thread"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM thread_participants 
        WHERE thread_tracker_key = ?
        ORDER BY first_seen_at ASC
    """).fetchall()
    return [dict(row) for row in rows]

# --------------------
# Cleanup and utility functions
# --------------------
def close_connection():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None

def cleanup_old_moderation_entries(days: int = 30):
    """Clean up old moderation entries"""
    conn = get_connection()
    with conn:
        conn.execute("""
            DELETE FROM moderation_queue 
            WHERE submitted_at < date('now', '-{} days') AND status != 'pending'
        """.format(days))
        
        conn.execute("""
            DELETE FROM rejected_checklists 
            WHERE rejected_at < date('now', '-{} days')
        """.format(days))

def get_all_county_regions():
    """Get all county regions from co_county_lookup"""
    from co_county_lookup import get_all_county_regions
    return get_all_county_regions()

def get_merge_candidates(species: str, lat: float, lon: float, region: str, distance_km: float = 4.0) -> List[Dict]:
    """Get merge candidates for a species within distance and same county"""
    conn = get_connection()
    
    # Get existing threads for this species in the same region
    rows = conn.execute("""
        SELECT DISTINCT t.tracker_key, c.lat, c.lon, c.location, c.obs_datetime
        FROM threads t
        JOIN checklists c ON t.tracker_key = c.thread_tracker_key
        WHERE c.species = ? AND c.region = ? AND c.lat IS NOT NULL AND c.lon IS NOT NULL
    """, (species, region)).fetchall()
    
    candidates = []
    for row in rows:
        distance = haversine(lat, lon, row['lat'], row['lon'])
        if distance <= distance_km:
            candidates.append({
                'thread_tracker_key': row['tracker_key'],
                'species': species,
                'location': row['location'],
                'distance': distance,
                'obs_datetime': row['obs_datetime']
            })
    
    # Sort by distance
    candidates.sort(key=lambda x: x['distance'])
    return candidates

def update_moderation_status(checklist_id: str, status: str, moderated_by: str = None, 
                           moderated_at: datetime = None, species: str = None,
                           merge_target_thread: str = None, rejection_reason: str = None):
    """Update moderation status - fixed parameter order to match usage"""
    conn = get_connection()
    
    if moderated_at is None:
        moderated_at = datetime.now(timezone.utc)
    
    with conn:
        if species:
            # Update specific species for checklist
            conn.execute("""
                UPDATE moderation_queue
                SET status=?, moderated_by=?, moderated_at=?, merge_target_thread=?, rejection_reason=?
                WHERE checklist_id=? AND species=?
            """, (status, moderated_by, moderated_at.isoformat(), 
                  merge_target_thread, rejection_reason, checklist_id, species))
        else:
            # Update all entries for checklist
            conn.execute("""
                UPDATE moderation_queue
                SET status=?, moderated_by=?, moderated_at=?, merge_target_thread=?, rejection_reason=?
                WHERE checklist_id=?
            """, (status, moderated_by, moderated_at.isoformat(), 
                  merge_target_thread, rejection_reason, checklist_id))
        
        # If rejected, add to rejected_checklists table
        if status == 'rejected':
            try:
                if species:
                    conn.execute("""
                        INSERT OR IGNORE INTO rejected_checklists (checklist_id, species, rejected_by)
                        VALUES (?, ?, ?)
                    """, (checklist_id, species, moderated_by))
                else:
                    # Get all species for this checklist and reject them
                    species_rows = conn.execute("""
                        SELECT DISTINCT species FROM moderation_queue WHERE checklist_id = ?
                    """, (checklist_id,)).fetchall()
                    
                    for species_row in species_rows:
                        conn.execute("""
                            INSERT OR IGNORE INTO rejected_checklists (checklist_id, species, rejected_by)
                            VALUES (?, ?, ?)
                        """, (checklist_id, species_row['species'], moderated_by))
            except sqlite3.IntegrityError:
                pass  # Already exists

def save_pending_checklist_with_aggregation(obs: Observation) -> bool:
    """Save to moderation queue with location-based aggregation"""
    from db import get_connection
    conn = get_connection()
    
    try:
        # Check if there's already a pending moderation for this species within 2km
        if obs.lat and obs.lon:
            existing_rows = conn.execute("""
                SELECT id, checklist_id, lat, lon FROM moderation_queue 
                WHERE species = ? AND status = 'pending' 
                AND lat IS NOT NULL AND lon IS NOT NULL
            """, (obs.species,)).fetchall()
            
            for row in existing_rows:
                existing_lat, existing_lon = row['lat'], row['lon']
                if haversine(obs.lat, obs.lon, existing_lat, existing_lon) <= 2.0:
                    # Within 2km of existing pending moderation - skip this one
                    logger.info(f"Skipping {obs.species} - within 2km of existing pending moderation")
                    return False
        
        # No nearby pending moderation found, add to queue
        with conn:
            conn.execute("""
                INSERT INTO moderation_queue (
                    checklist_id, species, species_code, region, observer, 
                    location, lat, lon, obs_datetime, has_media
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                obs.checklist_id, obs.species, getattr(obs, 'species_code', ''),
                obs.region, obs.observer, obs.location, obs.lat, obs.lon, 
                obs.obs_datetime.isoformat(), obs.has_media
            ))
        return True
        
    except Exception as e:
        logger.error(f"Error in save_pending_checklist_with_aggregation: {e}")
        return False

def get_merge_candidates(species: str, lat: float, lon: float, distance_km: float = 10.0) -> List[dict]:
    """Get potential merge candidates for moderation dropdown"""
    from db import get_connection
    conn = get_connection()
    
    candidates = []
    try:
        # Get threads for this species within distance
        rows = conn.execute("""
            SELECT DISTINCT t.tracker_key, t.last_seen_at, c.location, c.lat, c.lon
            FROM threads t
            JOIN checklists c ON t.tracker_key = c.thread_tracker_key
            WHERE c.species = ? AND c.lat IS NOT NULL AND c.lon IS NOT NULL
            ORDER BY t.last_seen_at DESC
            LIMIT 5
        """, (species,)).fetchall()
        
        for row in rows:
            thread_lat, thread_lon = row['lat'], row['lon']
            distance = haversine(lat, lon, thread_lat, thread_lon)
            if 2.0 < distance <= distance_km:  # Beyond auto-merge but within consideration
                candidates.append({
                    'tracker_key': row['tracker_key'],
                    'location': row['location'],
                    'distance_km': round(distance, 1),
                    'last_seen': row['last_seen_at']
                })
        
        return candidates
        
    except Exception as e:
        logger.error(f"Error getting merge candidates: {e}")
        return []