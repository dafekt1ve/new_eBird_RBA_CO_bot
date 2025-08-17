from dataclasses import dataclass
from datetime import datetime

@dataclass
class ThreadRecord:
    tracker_key: str
    thread_id: int
    type: str  # 'bot' or 'user'
    last_seen_at: datetime
    status_bucket: str

@dataclass
class Observation:
    checklist_id: str
    species: str
    region: str
    observer: str
    obs_datetime: datetime
    thread_tracker_key: str | None

@dataclass
class ChecklistModeration:
    checklist_id: str
    species: str
    region: str
    submitted_by: str | None
    submitted_at: datetime
    status: str  # 'pending', 'accepted', 'rejected'
    moderated_by: str | None

@dataclass
class MissedObservation:
    observer: str
    region: str
    species: str
    missed_at: datetime
    thread_tracker_key: str | None
