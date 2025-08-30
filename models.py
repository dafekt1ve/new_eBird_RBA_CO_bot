# models.py
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class ThreadType(str, Enum):
    BOT = "bot"
    USER = "user"

class ModerationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

@dataclass
class ThreadRecord:
    tracker_key: str
    thread_id: int
    type: ThreadType  # enforce BOT/USER
    last_seen_at: datetime
    status_bucket: str
    active_checklists: list[str] = field(default_factory=list)


@dataclass
class Observation:
    checklist_id: str
    species: str               # normalized species name (no subspecies)
    subspecies: str | None     # extracted subspecies name if present
    region: str
    location: str
    observer: str
    obs_datetime: datetime
    local_tz: str
    thread_tracker_key: str | None
    lat: float | None
    lon: float | None
    counted: bool = False
    has_media: bool = False


@dataclass
class ModerationEntry:
    """Enhanced moderation entry with full checklist data."""
    id: int
    checklist_id: str
    species: str
    region: str
    observer: str | None
    location: str | None
    obs_datetime: datetime
    local_tz: str
    lat: float | None
    lon: float | None
    has_media: bool
    submitted_at: datetime
    status: ModerationStatus
    moderated_by: str | None = None
    moderated_at: datetime | None = None
    discord_message_id: str | None = None


@dataclass
class ChecklistModeration:
    """Legacy moderation class for backward compatibility."""
    checklist_id: str
    species: str
    region: str
    submitted_by: str | None
    submitted_at: datetime
    status: ModerationStatus  # enforce PENDING/ACCEPTED/REJECTED
    moderated_by: str | None
    moderated_at: datetime | None = None
    merge_target_thread: str | None = None


@dataclass
class MissedObservation:
    observer: str
    region: str
    species: str
    missed_at: datetime
    thread_tracker_key: str | None
    related_checklist: str | None = None