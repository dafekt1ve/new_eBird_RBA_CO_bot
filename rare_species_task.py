# rare_species_task.py
import discord
from datetime import datetime
from typing import List
from ebird_api import fetch_ebird_rba
from mappers import build_observation_from_ebird
from moderation_utils import process_observations_for_moderation
from discord_moderation import send_moderation_requests
from models import Observation
import logging

logger = logging.getLogger("Dipper_RBA_Bot")

async def rare_species_moderation_task(bot: discord.Client, guild_id: int, region_code: str = "US-CO") -> int:
    """
    Task to check for rare species that need moderation.
    
    Args:
        bot: Discord bot instance
        guild_id: Discord guild ID
        region_code: eBird region code to check (default: Colorado)
        
    Returns:
        Number of new moderation requests sent
    """
    try:
        logger.info(f"Starting rare species moderation check for {region_code}")
        
        # Get the guild and moderation channel
        guild = bot.get_guild(guild_id)
        if not guild:
            logger.error(f"Guild {guild_id} not found")
            return 0
            
        # Find moderation channel
        moderation_channel = discord.utils.get(guild.channels, name="test-rba-moderation")
        if not moderation_channel:
            logger.error("Moderation channel not found")
            return 0
            
        # Fetch recent observations from eBird
        ebird_data = fetch_ebird_rba(region_code)
        if not ebird_data:
            logger.info("No eBird data returned")
            return 0
            
        logger.info(f"Fetched {len(ebird_data)} observations from eBird")
        
        # Convert eBird data to Observation objects
        observations = []
        for data in ebird_data:
            try:
                # We need to manually create observations since your build_observation_from_ebird
                # is missing some required fields. Let's create them directly.
                lat = data.get("lat")
                lon = data.get("lng")
                
                # Get timezone info
                try:
                    from time_utils import get_timezone_name
                    tz_name = get_timezone_name(lat, lon) if lat is not None and lon is not None else "UTC"
                except Exception:
                    tz_name = "UTC"

                # Parse observation datetime
                obs_datetime = None
                if data.get("obsDt"):
                    try:
                        obs_datetime = datetime.strptime(data["obsDt"], "%Y-%m-%d %H:%M")
                        # Convert to UTC
                        if lat is not None and lon is not None:
                            from time_utils import ebird_local_to_utc
                            obs_datetime = ebird_local_to_utc(obs_datetime.strftime("%Y-%m-%d %H:%M"), lat, lon)
                    except Exception as e:
                        logger.error(f"Error parsing observation datetime: {e}")
                        continue

                # Split species and subspecies
                from mappers import split_species_and_subspecies
                species, subspecies = split_species_and_subspecies(data.get("comName", "Unknown"))

                obs = Observation(
                    checklist_id=data.get("subId"),
                    species=species,
                    subspecies=subspecies,
                    region=region_code,
                    location=data.get("locName", "Unknown"),
                    observer=data.get("userDisplayName", "Unknown"),
                    obs_datetime=obs_datetime or datetime.now(),
                    local_tz=tz_name,
                    thread_tracker_key=f"{species}|{region_code}",
                    lat=lat,
                    lon=lon,
                    counted=data.get("howMany", 0) > 0,
                    has_media=bool(data.get("hasRichMedia", False))
                )
                observations.append(obs)
            except Exception as e:
                logger.error(f"Error building observation from eBird data: {e}")
                continue
        
        if not observations:
            logger.info("No valid observations to process")
            return 0
            
        logger.info(f"Built {len(observations)} observation objects")
        
        # Process observations and create moderation entries for rare species
        moderation_entries = process_observations_for_moderation(observations)
        
        if not moderation_entries:
            logger.info("No new rare species found requiring moderation")
            return 0
            
        logger.info(f"Created {len(moderation_entries)} moderation entries")
        
        # Send moderation requests to Discord
        sent_count = await send_moderation_requests(moderation_channel, moderation_entries)
        
        logger.info(f"Rare species moderation check complete. Sent {sent_count} requests.")
        return sent_count
        
    except Exception as e:
        logger.error(f"Error in rare species moderation task: {e}")
        return 0


async def process_single_checklist_for_moderation(bot: discord.Client, guild_id: int, checklist_id: str) -> int:
    """
    Process a single checklist for moderation (useful for testing or manual processing).
    
    Args:
        bot: Discord bot instance
        guild_id: Discord guild ID
        checklist_id: eBird checklist ID to process
        
    Returns:
        Number of new moderation requests sent
    """
    try:
        logger.info(f"Processing single checklist for moderation: {checklist_id}")
        
        # Get the guild and moderation channel
        guild = bot.get_guild(guild_id)
        if not guild:
            logger.error(f"Guild {guild_id} not found")
            return 0
            
        # Find moderation channel
        moderation_channel = discord.utils.get(guild.channels, name="test-rba-moderation")
        if not moderation_channel:
            logger.error("Moderation channel not found")
            return 0
        
        # For single checklist processing, we'd need to modify fetch_ebird_rba
        # to accept a checklist ID, or create a new function to fetch single checklists
        # This is a placeholder for that functionality
        logger.warning("Single checklist processing not yet implemented")
        return 0
        
    except Exception as e:
        logger.error(f"Error processing single checklist for moderation: {e}")
        return 0