# migrate_moderation_queue.py
import sqlite3

DB_FILE = "./data/dipper_bot.db"

def add_column_if_missing(conn, table: str, column: str, column_def: str):
    """Check if column exists, and add it if not."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if column not in columns:
        print(f"Adding column {column} to {table}...")
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
    else:
        print(f"Column {column} already exists in {table}, skipping.")

def migrate():
    conn = sqlite3.connect(DB_FILE)
    with conn:
        add_column_if_missing(conn, "moderation_queue", "moderated_at", "TEXT")
        add_column_if_missing(conn, "moderation_queue", "merge_target_thread", "TEXT")
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
