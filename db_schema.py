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
    thread_tracker_key TEXT,
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
    moderated_by TEXT
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
        connection.execute(MISSES_TABLE)
