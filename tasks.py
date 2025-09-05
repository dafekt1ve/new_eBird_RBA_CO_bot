#tasks.py - Fixed to work with current codebase structure
import discord
import os
from db import save_checklist, save_pending_checklist, get_moderation_entry
from co_review_loader import is_species_statewide_rba
from co_county_lookup import get_all_county_regions
from ebird_api import fetch_ebird_rba
from discord.ext import tasks
from discord_messages import build_cluster_moderation_message  # Now works with fixed discord_messages
from rba_formatter import cluster_observations, chunked_rba_messages
from time_utils import ebird_local_to_utc, get_timezone_name
from models import Observation, ChecklistModeration, ModerationStatus
import logging
from discord.utils import get

logger = logging.getLogger("Dipper_RBA_Bot")

STATEWIDE_CODE = "US-CO"
STATEWIDE_CHANNEL = int(os.getenv("MODERATION_CHANNEL_ID", 0))  # Updated to match bot_main

@tasks.loop(minutes=10)
async def statewide_rba_task(bot):
    logger.info("Running statewide RBA moderation check...")

    try:
        obs_list = fetch_ebird_rba(STATEWIDE_CODE)  # Returns list of dicts from eBird API
    except Exception as e:
        logger.error(f"Failed to fetch statewide obs: {e}")
        return

    channel = bot.get_channel(STATEWIDE_CHANNEL)
    if not channel:
        logger.error("Statewide moderation channel not found")
        return

    # Filter flagged species using common names
    flagged_obs = []
    for obs in obs_list:
        common_name = obs.get("comName")
        if common_name and is_species_statewide_rba(common_name):
            flagged_obs.append(obs)
    
    if not flagged_obs:
        logger.info("No flagged species found for moderation")
        return

    # Cluster by species + location (working with raw eBird data)
    clusters_dict = cluster_observations(flagged_obs, threshold_km=4)

    for cluster_observations_list in clusters_dict.values():
        # Skip clusters where all checklists are already pending
        new_cluster = [obs for obs in cluster_observations_list if not get_moderation_entry(obs.get("subId"))]
        if not new_cluster:
            continue

        # Save each checklist to moderation table
        for obs_dict in new_cluster:
            try:
                # Convert eBird dict to ChecklistModeration object
                obs_datetime = ebird_local_to_utc(
                    obs_dict.get("obsDt"), 
                    obs_dict.get("lat"), 
                    obs_dict.get("lng")
                )
                
                cm = ChecklistModeration(
                    checklist_id=obs_dict.get("subId"),
                    species=obs_dict.get("comName"),
                    region=obs_dict.get("locName", "US-CO"),
                    submitted_by=obs_dict.get("userDisplayName"),
                    submitted_at=obs_datetime,
                    status=ModerationStatus.PENDING,
                    moderated_by=None,
                )
                
                success = save_pending_checklist(cm)
                if success:
                    logger.info(f"Added {cm.species} from {cm.checklist_id} to moderation queue")
                
            except Exception as e:
                logger.error(f"Error saving moderation entry: {e}")
                continue

        # Build embed + view for the cluster
        try:
            embed, view = build_cluster_moderation_message(new_cluster)
            if embed and view:
                message = await channel.send(embed=embed, view=view)
                
                # Update database with Discord message ID
                for obs_dict in new_cluster:
                    checklist_id = obs_dict.get("subId")
                    if checklist_id:
                        from db import get_connection
                        conn = get_connection()
                        with conn:
                            conn.execute("""
                                UPDATE moderation_queue 
                                SET discord_message_id = ? 
                                WHERE checklist_id = ? AND discord_message_id IS NULL
                            """, (message.id, checklist_id))
                
                logger.info(f"Sent moderation message for cluster with {len(new_cluster)} observations")
                
        except Exception as e:
            logger.error(f"Error building/sending moderation message: {e}")

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
            logger.warning(f"[RBA] No channel found for {county_name} ({code})")

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

            logger.info(f"[RBA] Posted {len(recent_obs)} observations to {channel.name}")

        except Exception as e:
            logger.error(f"[RBA] Error processing region {region_code}: {e}")