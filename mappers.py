# mappers.py
import re
from datetime import datetime
from models import Observation
from time_utils import ebird_local_to_utc

def _normalize_obsdt(obs_dt_str: str | None) -> str | None:
    """eBird returns 'YYYY-MM-DD HH:MM' or sometimes with seconds. Truncate to minutes."""
    if not obs_dt_str:
        return None
    # Keep only up to minutes: 'YYYY-MM-DD HH:MM'
    return obs_dt_str[:16]

def split_species_and_subspecies(com_name: str) -> tuple[str, str | None]:
    """
    Split eBird common name into species and subspecies.
    Example:
      "Yellow-rumped Warbler (Myrtle)" 
        -> ("Yellow-rumped Warbler", "Myrtle")
      "Canada Goose"
        -> ("Canada Goose", None)
    """
    match = re.match(r"^(.*?)\s*\((.*?)\)$", com_name)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return com_name.strip(), None

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

    species, subspecies = split_species_and_subspecies(data.get("comName"))

    return Observation(
        checklist_id=data.get("subId"),
        species=species,
        subspecies=subspecies,
        region=data.get("region"),
        observer=data.get("observer"),
        obs_datetime=obs_datetime,
        thread_tracker_key=f"{data.get('species')}|{region_hint}",
        lat=lat,
        lon=lon,
        counted=data.get("counted", False),
        has_media=bool(data.get("hasRichMedia", []))
    )
