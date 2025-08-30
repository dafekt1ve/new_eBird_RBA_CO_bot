# tasks.py
import discord
import os
from db import save_checklist, get_all_county_regions
from ebird_api import fetch_ebird_rba
from rba_formatter import chunked_rba_messages
from time_utils import ebird_local_to_utc, get_timezone_name
from models import Observation, ChecklistModeration, ModerationStatus
from rare_species_task import rare_species_moderation_task
import logging
from discord.utils import get

logger = logging.getLogger("Dipper_RBA_Bot")

STATEWIDE_CODE = "US-CO"
STATEWIDE_CHANNEL = int(os.getenv("STATEWIDE_MOD_CHANNEL", 0))  # channel for mods

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
            logger.error(f"[RBA] No channel found for {county_name} ({code})")

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
                    subspecies=None,  # Will be extracted by mappers if needed
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

            logger.info(f"[RBA] Posted {len(recent_obs)} observations to {channel.name}")

        except Exception as e:
            logger.error(f"[RBA] Error processing region {region_code}: {e}")


async def moderation_task(bot: discord.Client, guild_id: int):
    """
    Task to check for rare species requiring moderation.
    This should be called every 10 minutes.
    """
    try:
        logger.info("[MODERATION] Starting rare species check")
        
        # Run the rare species moderation task for Colorado
        sent_count = await rare_species_moderation_task(bot, guild_id, "US-CO")
        
        if sent_count > 0:
            logger.info(f"[MODERATION] Sent {sent_count} new moderation requests")
        else:
            logger.debug("[MODERATION] No new moderation requests needed")
            
    except Exception as e:
        logger.error(f"[MODERATION] Error in moderation task: {e}")