# thread_rba_content.py
from datetime import datetime, timezone
from typing import List
import sqlite3
from rba_formatter import chunked_rba_messages
from models import Observation
from dotenv import load_dotenv
import os

load_dotenv()
DB_FILE = os.getenv("DB_FILE")
DB_PATH = f"./data/{DB_FILE}"

def get_accepted_checklists_for_cluster(cluster_id: str) -> List[Observation]:
    """Fetch accepted checklists for a cluster from the DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT c.checklist_id, c.species, c.region, c.location, c.observer,
               c.obs_datetime, c.local_tz, c.thread_tracker_key, c.lat, c.lon,
               c.counted, c.has_media
        FROM checklists c
        JOIN moderation_queue m
          ON c.checklist_id = m.checklist_id
        WHERE c.thread_tracker_key = ?
          AND m.status = 'accepted'
    """, (cluster_id,))

    rows = cur.fetchall()
    conn.close()

    observations: List[Observation] = []
    for r in rows:
        obs_dt = datetime.fromisoformat(r["obs_datetime"])
        observations.append(
            Observation(
                checklist_id=r["checklist_id"],
                species=r["species"],
                region=r["region"],
                location=r["location"],
                observer=r["observer"],
                obs_datetime=obs_dt,
                local_tz=r.get("local_tz", "UTC"),
                thread_tracker_key=r["thread_tracker_key"],
                lat=r.get("lat"),
                lon=r.get("lon"),
                counted=bool(r.get("counted", False)),
                has_media=bool(r.get("has_media", False))
            )
        )

    return observations

def build_rba_content_from_cluster(cluster_id: str) -> List[str]:
    """Return a list of Discord-formatted RBA messages for a given cluster."""
    print(cluster_id)
    observations = get_accepted_checklists_for_cluster(cluster_id)
    if not observations:
        return ["No accepted checklists for this cluster."]
    return chunked_rba_messages(observations)
