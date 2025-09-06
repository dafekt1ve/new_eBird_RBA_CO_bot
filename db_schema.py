# db_schema.py - Enhanced with moderation functionality
import sqlite3

# Threads table
THREADS_TABLE = """
CREATE TABLE IF NOT EXISTS threads (
    tracker_key TEXT PRIMARY KEY,
    thread_id INTEGER NOT NULL,
    discord_channel_id INTEGER,
    discord_message_id INTEGER,
    type TEXT CHECK(type IN ('bot','user')) NOT NULL,
    last_seen_at TEXT NOT NULL,
    status_bucket TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# Checklists table
CHECKLISTS_TABLE = """
CREATE TABLE IF NOT EXISTS checklists (
    checklist_id TEXT PRIMARY KEY,
    species TEXT NOT NULL,
    species_code TEXT,
    region TEXT NOT NULL,
    observer TEXT,
    obs_datetime TEXT NOT NULL,
    thread_tracker_key TEXT,
    lat REAL,
    lon REAL,
    location TEXT,
    has_media BOOLEAN DEFAULT 0,
    local_tz TEXT,
    processed_for_moderation BOOLEAN DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(thread_tracker_key) REFERENCES threads(tracker_key)
);
"""

# Moderation queue table - Enhanced (without species_code column)
MODERATION_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS moderation_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id TEXT NOT NULL,
    species TEXT NOT NULL,
    region TEXT NOT NULL,
    observer TEXT,
    location TEXT,
    lat REAL,
    lon REAL,
    obs_datetime TEXT NOT NULL,
    submitted_by TEXT,
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT CHECK(status IN ('pending','accepted','rejected')) DEFAULT 'pending',
    moderated_by TEXT,
    moderated_at TEXT,
    discord_message_id INTEGER,
    merge_target_thread TEXT,
    rejection_reason TEXT,
    UNIQUE(checklist_id, species) -- Prevent duplicate moderation for same checklist+species
);
"""

# Rejected checklists table - Track rejected items to avoid re-moderation
REJECTED_CHECKLISTS_TABLE = """
CREATE TABLE IF NOT EXISTS rejected_checklists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id TEXT NOT NULL,
    species TEXT NOT NULL,
    species_code TEXT,
    rejected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    rejected_by TEXT,
    UNIQUE(checklist_id, species)
);
"""

# Statewide RBA thresholds table
STATEWIDE_THRESHOLDS_TABLE = """
CREATE TABLE IF NOT EXISTS statewide_thresholds (
    common_name TEXT PRIMARY KEY,
    is_review_species BOOLEAN DEFAULT 0
);
"""

# Thread participants table - Track who has seen each bird
THREAD_PARTICIPANTS_TABLE = """
CREATE TABLE IF NOT EXISTS thread_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_tracker_key TEXT NOT NULL,
    observer TEXT NOT NULL,
    checklist_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(thread_tracker_key, observer),
    FOREIGN KEY(thread_tracker_key) REFERENCES threads(tracker_key)
);
"""

# Misses table
MISSES_TABLE = """
CREATE TABLE IF NOT EXISTS misses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer TEXT NOT NULL,
    region TEXT NOT NULL,
    species TEXT NOT NULL,
    missed_at TEXT NOT NULL,
    thread_tracker_key TEXT,
    FOREIGN KEY(thread_tracker_key) REFERENCES threads(tracker_key)
);
"""

# Helper function to initialize all tables
def init_db(connection):
    with connection:
        connection.execute(THREADS_TABLE)
        connection.execute(CHECKLISTS_TABLE)
        connection.execute(MODERATION_QUEUE_TABLE)
        connection.execute(REJECTED_CHECKLISTS_TABLE)
        connection.execute(STATEWIDE_THRESHOLDS_TABLE)
        connection.execute(THREAD_PARTICIPANTS_TABLE)
        connection.execute(MISSES_TABLE)

# Run when executed directly
if __name__ == "__main__":
    db_file = "dipper_bot.db"
    conn = sqlite3.connect(db_file)
    print(f"Initializing database at {db_file}...")
    init_db(conn)
    conn.close()
    print("Done.")