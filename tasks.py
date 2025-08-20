#tasks.py
import discord
from db import save_checklist, get_all_county_regions
from ebird_api import fetch_ebird_rba
from discord_messages import chunked_rba_messages
from time_utils import ebird_local_to_utc, get_timezone_name
from models import Observation

async def build_region_channels_map(guild: discord.Guild):
    """
    Build a dict mapping region_code -> Discord channel object.
    Assumes your channels are named like "el-paso-rba" for "El Paso" county.
    """
    regions = get_all_county_regions()  # [{"code": ..., "name": ...}]
    region_channels = {}

    for region in regions:
        code = region["code"]
        county_name = region["name"]
        normalized_name = county_name.lower().replace(" ", "-")
        expected_channel_name = f"{normalized_name}-rba"

        channel = discord.utils.get(guild.text_channels, name=expected_channel_name)
        if channel:
            region_channels[code] = channel
        else:
            print(f"[RBA] No channel found for {county_name} ({code})")

    return region_channels

async def rba_task(region_channels: dict):
    """
    Fetch RBA for all counties and post notable observations to their corresponding channel.
    """
    for region_code, channel in region_channels.items():
        try:
            recent_obs_dicts = fetch_ebird_rba(region_code)
            recent_obs = []

            for d in recent_obs_dicts:
                lat = d.get("lat")
                lon = d.get("lng")
                try:
                    tz_name = get_timezone_name(lat, lon) if lat is not None and lon is not None else "UTC"
                except Exception:
                    tz_name = "UTC"

                try:
                    obs_utc = ebird_local_to_utc(d.get("obsDt"), lat, lon)
                except Exception:
                    continue  # Skip malformed dates

                obs = Observation(
                    checklist_id=d.get("subId"),
                    species=d.get("comName"),
                    region=region_code,
                    location=d.get("locName", "Unknown"),
                    observer=d.get("userDisplayName", "Unknown"),
                    obs_datetime=obs_utc,
                    local_tz=tz_name,
                    thread_tracker_key=None,
                    lat=lat,
                    lon=lon,
                    has_media=bool(d.get("hasRichMedia", []))
                )
                recent_obs.append(obs)

            if recent_obs:
                messages = chunked_rba_messages(recent_obs)
                for msg in messages:
                    await channel.send(msg, silent=True)

            # Save checklists regardless of posting
            for obs in recent_obs:
                save_checklist(obs)

            print(f"[RBA] Posted {len(recent_obs)} observations to {channel.name}")

        except Exception as e:
            print(f"[RBA] Error processing region {region_code}: {e}")