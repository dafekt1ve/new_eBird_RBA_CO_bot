from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import radians, sin, cos, sqrt, atan2
from zoneinfo import ZoneInfo

MAX_DISCORD_MSG_LEN = 2000
RECENT_HOURS = 24

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

def normalize_species_name(name: str) -> str:
    base = name.split("(")[0].strip()
    return "".join(c.lower() for c in base if c.isalnum() or c.isspace())

def cluster_observations(observations, threshold_km=2):
    clusters = {}  # key -> list[obs]

    for obs in observations:
        norm_name = normalize_species_name(obs.species)
        lat, lon = obs.lat, obs.lon

        match_key = None
        for (sp, clat, clon, loc) in clusters.keys():
            if normalize_species_name(sp) != norm_name:
                continue
            if haversine(lat, lon, clat, clon) <= threshold_km:
                match_key = (sp, clat, clon, loc)
                break

        if match_key:
            clusters[match_key].append(obs)
        else:
            key = (obs.species, lat, lon, obs.location or "Unknown")
            clusters[key] = [obs]

    return clusters

def chunked_rba_messages(observations: list) -> list[str]:
    """
    Build Discord messages (<=2000 chars) showing:
      - most recent checklist per clustered species@location
      - 'Also reported in last 24 hours by' if additional observers
      - 📷 icon for media
    """
    if not observations:
        return ["No notable observations in this region."]

    clusters = cluster_observations(observations)
    messages: list[str] = []
    current_lines: list[str] = []

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=RECENT_HOURS)

    for (species_name, lat, lon, location) in sorted(clusters.keys()):
        obs_list = clusters[(species_name, lat, lon, location)]
        # Sort newest → oldest
        obs_sorted = sorted(obs_list, key=lambda o: o.obs_datetime, reverse=True)

        # Filter to recent obs
        recent_obs = [o for o in obs_sorted if o.obs_datetime >= cutoff]
        if not recent_obs:
            continue  # skip clusters with nothing recent

        most_recent = recent_obs[0]

        local_dt = most_recent.obs_datetime.astimezone(ZoneInfo(most_recent.local_tz))
        first_dt = local_dt.strftime("%Y-%m-%d %H:%M")
        first_link = f"https://ebird.org/checklist/{most_recent.checklist_id}"
        media_icon = "📷" if most_recent.has_media else ""

        block_lines: list[str] = []
        block_lines.append(
            f"__**{species_name}**__ @ [{location}](<https://www.google.com/maps/search/?api=1&query={lat},{lon}>) {media_icon}"
        )
        block_lines.append(
            f"▸ [{first_dt}](<{first_link}>) by [{most_recent.observer}](<{first_link}>) {media_icon}"
        )

        # Additional observers in last 24 hours
        recent_others = [o for o in recent_obs if o != most_recent]
        recent_others_sorted = sorted(recent_others, key=lambda o: o.obs_datetime, reverse=True)
        if recent_others:
            seen = set()
            unique_recent = []
            for o in recent_others_sorted:
                if o.observer not in seen:
                    unique_recent.append(o)
                    seen.add(o.observer)

            displayed = unique_recent[:10]
            more_count = max(0, len(unique_recent) - len(displayed))

            observers_str = ", ".join(
                f"[{o.observer}](<https://ebird.org/checklist/{o.checklist_id}>)" + (" 📷" if o.has_media else "")
                for o in displayed
            )
            line = f"▸ Also reported in last 24 hours by: {observers_str}"
            if more_count:
                line += f", and {more_count} more"
            block_lines.append(line)

        block_lines.append("")  # blank line between clusters

        # Keep cluster together in one message
        block_size = sum(len(line) + 1 for line in block_lines)
        if sum(len(l) + 1 for l in current_lines) + block_size > MAX_DISCORD_MSG_LEN:
            messages.append("\n".join(current_lines))
            current_lines = []

        current_lines.extend(block_lines)

    if current_lines:
        messages.append("\n".join(current_lines))

    return messages
