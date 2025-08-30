# db_schema.py
import sqlite3
from dotenv import load_dotenv
import os

load_dotenv()
db_file = os.getenv("DB_FILE")
db_path = f"./data/{db_file}"

# Threads table
THREADS_TABLE = """
CREATE TABLE IF NOT EXISTS threads (
    tracker_key TEXT PRIMARY KEY,
    thread_id INTEGER NOT NULL,
    type TEXT CHECK(type IN ('bot','user')) NOT NULL,
    last_seen_at TEXT NOT NULL,
    status_bucket TEXT NOT NULL
);
"""

# Checklists table
CHECKLISTS_TABLE = """
CREATE TABLE IF NOT EXISTS checklists (
    checklist_id TEXT PRIMARY KEY,
    species TEXT NOT NULL,
    region TEXT NOT NULL,
    observer TEXT,
    obs_datetime TEXT NOT NULL,
    local_tz TEXT DEFAULT 'UTC',
    location TEXT,
    lat TEXT,
    lon TEXT,
    thread_tracker_key TEXT,
    counted BOOLEAN DEFAULT FALSE,
    has_media BOOLEAN DEFAULT FALSE,
    FOREIGN KEY(thread_tracker_key) REFERENCES threads(tracker_key)
);
"""

# Moderation queue table
MODERATION_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS moderation_queue (
    checklist_id TEXT PRIMARY KEY,
    species TEXT NOT NULL,
    region TEXT NOT NULL,
    submitted_by TEXT,
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT CHECK(status IN ('pending','accepted','rejected')) DEFAULT 'pending',
    moderated_by TEXT,
    moderated_at TEXT,
    merge_target_thread TEXT
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

# State Review List Table
STATE_REVIEW_SPECIES_TABLE = """
CREATE TABLE IF NOT EXISTS state_review_species (
    species_name TEXT PRIMARY KEY,
    review_flag BOOLEAN NOT NULL
);
"""

# Helper function to initialize all tables
def init_db(connection):
    with connection:
        connection.execute(THREADS_TABLE)
        connection.execute(CHECKLISTS_TABLE)
        connection.execute(MODERATION_QUEUE_TABLE)
        connection.execute(MISSES_TABLE)
        connection.execute(STATE_REVIEW_SPECIES_TABLE)

# Run when executed directly
if __name__ == "__main__":
    conn = sqlite3.connect(db_path)
    print(f"Initializing database at {db_path}...")
    init_db(conn)
    conn.close()
    print("Done.")
