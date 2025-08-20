# time_utils.py
from datetime import datetime
from timezonefinder import TimezoneFinder
import pytz

tf = TimezoneFinder()
_tz_cache = {}  # simple in-memory cache

def get_timezone_name(lat: float, lon: float) -> str:
    """Return timezone name for given coordinates, using cache."""
    if lat is None or lon is None:
        raise ValueError("Latitude and longitude must be provided for timezone lookup.")

    key = (round(lat, 4), round(lon, 4))  # round to reduce duplicates
    if key in _tz_cache:
        return _tz_cache[key]

    tz_name = tf.timezone_at(lat=lat, lng=lon)
    if not tz_name:
        tz_name = 'UTC'  # fallback to UTC if unknown

    _tz_cache[key] = tz_name
    return tz_name

def ebird_local_to_utc(obs_datetime, lat: float, lon: float) -> datetime:
    """
    Convert an eBird observation datetime (naive local) to UTC using lat/lon.
    
    Parameters:
        obs_datetime: str in "%Y-%m-%d %H:%M" format or naive datetime
        lat, lon: coordinates of the observation
    Returns:
        UTC-aware datetime
    """
    if isinstance(obs_datetime, str):
        naive_local = datetime.strptime(obs_datetime, "%Y-%m-%d %H:%M")
    elif isinstance(obs_datetime, datetime):
        naive_local = obs_datetime
    else:
        raise TypeError("obs_datetime must be str or datetime")

    tz_name = get_timezone_name(lat, lon)
    local_tz = pytz.timezone(tz_name)
    aware_local = local_tz.localize(naive_local)
    utc_dt = aware_local.astimezone(pytz.UTC)
    return utc_dt
