# mappers.py
from datetime import datetime
from models import Observation
from time_utils import ebird_local_to_utc

def _normalize_obsdt(obs_dt_str: str | None) -> str | None:
    """eBird returns 'YYYY-MM-DD HH:MM' or sometimes with seconds. Truncate to minutes."""
    if not obs_dt_str:
        return None
    # Keep only up to minutes: 'YYYY-MM-DD HH:MM'
    return obs_dt_str[:16]

def build_observation_from_ebird(data: dict, region_hint: str, convert_to_utc: bool = True) -> Observation:
    """
    Map a raw eBird dict to an Observation object.

    Args:
        data: Raw dict from eBird API
        region_hint: Used for thread_tracker_key if needed
        convert_to_utc: Whether to convert obs_datetime from local to UTC
    """
    obs_datetime = None
    if data.get("obsDt"):
        obs_datetime = datetime.strptime(data["obsDt"], "%Y-%m-%d %H:%M")

    lat = data.get("lat")
    lon = data.get("lng")

    if convert_to_utc and obs_datetime and lat is not None and lon is not None:
        obs_datetime = ebird_local_to_utc(obs_datetime.strftime("%Y-%m-%d %H:%M"), lat, lon)

    return Observation(
        checklist_id=data.get("subId"),
        species=data.get("species"),
        region=data.get("region"),
        observer=data.get("observer"),
        obs_datetime=obs_datetime,
        thread_tracker_key=f"{data.get('species')}|{region_hint}",
        lat=lat,
        lon=lon,
    )
