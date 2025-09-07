# moderation.py - Complete moderation system with datetime fixes
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
from time import time
import asyncio
import logging
from typing import List, Dict, Optional
from datetime import timedelta
from urllib.parse import quote

from db import (
    save_pending_checklist, get_pending_moderation, update_moderation_status,
    is_checklist_rejected, find_nearby_thread, save_thread, add_thread_participant,
    mark_checklist_processed_for_moderation,
    get_moderation_item_by_message_id, get_merge_candidates
)
from co_review_loader import is_species_statewide_rba, get_species_review_status
from models import Observation, ThreadRecord
from ebird_api import fetch_ebird_rba
from time_utils import ebird_local_to_utc, get_timezone_name
from geo_utils import normalize_species_name, haversine

logger = logging.getLogger("Dipper_RBA_Bot")

def safe_datetime_parse(dt_input):
    """
    Safely parse datetime input that could be a string, datetime, or dict.
    Returns a timezone-aware datetime object or None if parsing fails.
    """
    if dt_input is None:
        return None
    
    if isinstance(dt_input, datetime):
        # Ensure it's timezone-aware
        if dt_input.tzinfo is None:
            return dt_input.replace(tzinfo=timezone.utc)
        return dt_input
    
    if isinstance(dt_input, str):
        try:
            # Try parsing ISO format first (handles timezone info)
            if 'T' in dt_input or '+' in dt_input or dt_input.endswith('Z'):
                return datetime.fromisoformat(dt_input.replace('Z', '+00:00'))
            # Try eBird format (naive datetime)
            naive_dt = datetime.strptime(dt_input, "%Y-%m-%d %H:%M")
            return naive_dt.replace(tzinfo=timezone.utc)
        except ValueError as e:
            logger.error(f"Could not parse datetime string '{dt_input}': {e}")
            return None
    
    logger.error(f"Unexpected datetime input type: {type(dt_input)} - {dt_input}")
    return None

class ModerationView(discord.ui.View):
    """Persistent view for moderation buttons with merge dropdown"""
    
    def __init__(self, merge_candidates: List[Dict] = None):
        super().__init__(timeout=None)
        
        # Add merge dropdown if there are candidates
        if merge_candidates:
            self.add_item(MergeDropdown(merge_candidates))
    
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="mod_accept", emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_moderation(interaction, "accepted")
    
    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="mod_reject", emoji="❌")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_moderation(interaction, "rejected")
    
    async def handle_moderation(self, interaction: discord.Interaction, action: str):
        """Handle accept/reject button clicks"""
        try:
            mod_item = get_moderation_item_by_message_id(interaction.message.id)
            if not mod_item:
                await interaction.response.send_message("⚠️ Could not find moderation item.", ephemeral=True)
                return
            
            if mod_item['status'] != 'pending':
                await interaction.response.send_message(
                    f"⚠️ This item has already been {mod_item['status']} by {mod_item.get('moderated_by', 'someone')}", 
                    ephemeral=True
                )
                return
            
            moderator = str(interaction.user)
            
            if action == "accepted":
                success = await self.handle_acceptance(mod_item, moderator, interaction)
                if success:
                    embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
                    embed.color = 0x00ff00  # Green
                    embed.set_footer(text=f"✅ ACCEPTED by {moderator}")
                    await interaction.response.edit_message(embed=embed, view=None)
                else:
                    await interaction.response.send_message("❌ Error processing acceptance.", ephemeral=True)
            
            elif action == "rejected":
                update_moderation_status(
                    mod_item['checklist_id'], 'rejected', moderator, species=mod_item['species']
                )
                embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
                embed.color = 0xff0000  # Red
                embed.set_footer(text=f"❌ REJECTED by {moderator}")
                await interaction.response.edit_message(embed=embed, view=None)
                logger.info(f"Rejected: {mod_item['species']} from {mod_item['checklist_id']} by {moderator}")
        
        except Exception as e:
            logger.error(f"Error in moderation handling: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred.", ephemeral=True)
            else:
                await interaction.followup.send("❌ An error occurred.", ephemeral=True)
    
    async def handle_acceptance(self, mod_item: dict, moderator: str, interaction: discord.Interaction) -> bool:
        """Handle acceptance logic - create or merge with thread"""
        try:
            species = mod_item['species']
            lat, lon = mod_item.get('lat'), mod_item.get('lon')
            region = mod_item['region']
            
            # CRITICAL FIX: Parse datetime first, then convert to UTC if needed
            obs_datetime_raw = mod_item.get('obs_datetime')
            obs_datetime = safe_datetime_parse(obs_datetime_raw)

            if obs_datetime is None:
                logger.error(f"Could not parse obs_datetime: {obs_datetime_raw}")
                obs_datetime = datetime.now(timezone.utc)  # Fallback to current time
            else:
                # If we successfully parsed it, ensure it's in UTC
                if obs_datetime.tzinfo is None:
                    # No timezone info, need to convert from local to UTC
                    obs_datetime = ebird_local_to_utc(obs_datetime, lat, lon)
                elif obs_datetime.tzinfo != timezone.utc:
                    # Has timezone but not UTC, convert to UTC
                    obs_datetime = obs_datetime.astimezone(timezone.utc)
            
            # Check for nearby existing thread
            existing_thread_key = None
            if lat is not None and lon is not None:
                existing_thread_key = find_nearby_thread(species, lat, lon, distance_km=4.0)
            
            if existing_thread_key:
                thread_tracker_key = existing_thread_key
                logger.info(f"Merging {species} with existing thread {thread_tracker_key}")
            else:
                thread_tracker_key = f"{species}|{region}|{int(time())}"
                
                # Create thread in statewide forum
                success = await self.create_statewide_thread(mod_item, thread_tracker_key, interaction)
                if not success:
                    return False
            
            # Add participant to thread
            add_thread_participant(
                thread_tracker_key, 
                mod_item.get('observer', 'Unknown'), 
                mod_item['checklist_id'], 
                obs_datetime
            )
            
            # Update moderation status
            update_moderation_status(
                mod_item['checklist_id'], 'accepted', moderator, 
                species=mod_item['species'], merge_target_thread=thread_tracker_key
            )
            
            logger.info(f"Accepted: {species} from {mod_item['checklist_id']} by {moderator}")
            return True
            
        except Exception as e:
            logger.error(f"Error in acceptance handling: {e}")
            return False
    
    async def create_statewide_thread(self, mod_item: dict, thread_tracker_key: str, interaction: discord.Interaction) -> bool:
        """Create new thread in statewide forum"""
        try:
            statewide_forum_channel_id = getattr(interaction.client, 'statewide_forum_channel_id', None)
            if not statewide_forum_channel_id:
                logger.error("Statewide forum channel ID not configured")
                return False
                
            forum_channel = interaction.guild.get_channel(int(statewide_forum_channel_id))
            if not forum_channel or not isinstance(forum_channel, discord.ForumChannel):
                logger.error(f"Forum channel not found or wrong type: {statewide_forum_channel_id}")
                return False
            
            # Parse datetime for thread record
            obs_datetime = safe_datetime_parse(mod_item.get('obs_datetime'))
            if obs_datetime is None:
                obs_datetime = datetime.now(timezone.utc)

            region_str = mod_item.get('region', 'Unknown Region')
            thread_name = f"{mod_item['species']}, {region_str}, {obs_datetime.strftime('%b %Y')}"

            embed = await create_thread_embed(mod_item)
            
            content, view = create_enhanced_thread_content(mod_item)
            thread = await forum_channel.create_thread(
                name=thread_name,
                content=content,
                view=view
            )
                        
            thread_record = ThreadRecord(
                tracker_key=thread_tracker_key,
                thread_id=thread.thread.id,
                type="bot",
                last_seen_at=obs_datetime,
                status_bucket="<24h"
            )
            thread_record.discord_channel_id = thread.thread.id
            save_thread(thread_record)
            
            logger.info(f"Created new thread {thread_tracker_key} in forum")
            return True
            
        except Exception as e:
            logger.error(f"Error creating statewide thread: {e}")
            return False

class MergeDropdown(discord.ui.Select):
    """Dropdown for selecting merge target"""
    
    def __init__(self, merge_candidates: List[Dict]):
        options = []
        for candidate in merge_candidates[:25]:  # Discord limit
            options.append(discord.SelectOption(
                label=f"{candidate['species']} - {candidate['location'][:50]}",
                description=f"Distance: {candidate['distance']:.1f}km",
                value=candidate['thread_tracker_key']
            ))
        
        super().__init__(
            placeholder="Select thread to merge with...",
            options=options,
            custom_id="merge_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle merge selection"""
        try:
            target_thread = self.values[0]
            mod_item = get_moderation_item_by_message_id(interaction.message.id)
            
            if not mod_item:
                await interaction.response.send_message("⚠️ Could not find moderation item.", ephemeral=True)
                return
            
            moderator = str(interaction.user)
            
            # Add to existing thread
            obs_datetime = safe_datetime_parse(mod_item.get('obs_datetime'))
            if obs_datetime is None:
                obs_datetime = datetime.now(timezone.utc)
            
            add_thread_participant(
                target_thread,
                mod_item.get('observer', 'Unknown'),
                mod_item['checklist_id'],
                obs_datetime
            )
            
            # Update moderation status
            update_moderation_status(
                mod_item['checklist_id'], 'accepted', moderator,
                species=mod_item['species'], merge_target_thread=target_thread
            )
            
            await interaction.response.edit_message(
                content=f"✅ **MERGED** with existing thread by {moderator}\n\n",
                embed=interaction.message.embeds[0] if interaction.message.embeds else None,
                view=None
            )
            
        except Exception as e:
            logger.error(f"Error in merge handling: {e}")
            await interaction.response.send_message("❌ Error processing merge.", ephemeral=True)

async def create_thread_embed(mod_item: dict) -> discord.Embed:
    """Create embed for thread post"""
    # CRITICAL FIX: Parse datetime first, then convert to UTC if needed
    obs_datetime_raw = mod_item.get('obs_datetime')
    obs_datetime = safe_datetime_parse(obs_datetime_raw)

    if obs_datetime is None:
        logger.error(f"Could not parse obs_datetime: {obs_datetime_raw}")
        obs_datetime = datetime.now(timezone.utc)  # Fallback to current time
    else:
        # If we successfully parsed it, ensure it's in UTC
        if obs_datetime.tzinfo is None:
            # No timezone info, need to convert from local to UTC
            obs_datetime = ebird_local_to_utc(obs_datetime, mod_item['lat'], mod_item['lon'])
        elif obs_datetime.tzinfo != timezone.utc:
            # Has timezone but not UTC, convert to UTC
            obs_datetime = obs_datetime.astimezone(timezone.utc)
    
    embed = discord.Embed(
        title=f"{mod_item['species']}, {mod_item['region']}, {obs_datetime.strftime('%b %Y')}",
        color=0xffaa00,
        timestamp=obs_datetime
    )
    
    observer_link = f"[{mod_item.get('observer', 'Unknown')}](<https://ebird.org/checklist/{mod_item['checklist_id']}>)"
    embed.add_field(name="Observer", value=observer_link, inline=True)
    
    if mod_item.get('lat') and mod_item.get('lon'):
        location_link = f"[{mod_item.get('location', 'Unknown')}](<https://www.google.com/maps/search/?api=1&query={mod_item['lat']},{mod_item['lon']}>)"
        embed.add_field(name="Location", value=location_link, inline=True)
    else:
        embed.add_field(name="Location", value=mod_item.get('location', 'Unknown'), inline=True)
    
    embed.add_field(name="County", value=mod_item.get('region', 'Unknown'), inline=True)
    
    return embed

class ModerationSystem:
    """Main moderation system class"""
    
    def __init__(self, bot: commands.Bot, moderation_channel_id: int, statewide_forum_channel_id: int):
        self.bot = bot
        self.moderation_channel_id = moderation_channel_id
        self.statewide_forum_channel_id = statewide_forum_channel_id
        
        # Store channel IDs on bot for access in views
        self.bot.moderation_channel_id = moderation_channel_id
        self.bot.statewide_forum_channel_id = statewide_forum_channel_id
        
        # Start background tasks
        self.process_statewide_rba.start()
        self.update_thread_recency.start()
        
        # Add persistent view
        self.bot.add_view(ModerationView())
    
    def cog_unload(self):
        """Clean up when unloading"""
        self.process_statewide_rba.cancel()
        self.update_thread_recency.cancel()
    
    @tasks.loop(minutes=10)
    async def process_statewide_rba(self):
        """Main loop - check for statewide RBAs and process moderation"""
        try:
            logger.info("Starting statewide RBA processing...")
            
            statewide_obs = await self.fetch_statewide_observations()
            
            # Group observations by species and proximity for clustering
            clusters = self.cluster_observations(statewide_obs, radius_km=2.0)
            
            # Process each cluster
            for cluster in clusters:
                await self.process_cluster_for_moderation(cluster)
                
            # Process pending moderation queue
            await self.process_pending_moderation()
            
            logger.info("Completed statewide RBA processing")
            
        except Exception as e:
            logger.error(f"Error in statewide RBA processing: {e}")
    
    def cluster_observations(self, observations: List[Observation], radius_km: float = 2.0) -> List[List[Observation]]:
        """Cluster observations by species and proximity"""
        clusters = []
        processed = set()
        
        for i, obs in enumerate(observations):
            if i in processed:
                continue
                
            cluster = [obs]
            processed.add(i)
            
            # Find nearby observations of same species
            for j, other_obs in enumerate(observations[i+1:], i+1):
                if j in processed:
                    continue
                    
                if (obs.species == other_obs.species and 
                    obs.lat and obs.lon and other_obs.lat and other_obs.lon and
                    haversine(obs.lat, obs.lon, other_obs.lat, other_obs.lon) <= radius_km):
                    cluster.append(other_obs)
                    processed.add(j)
            
            clusters.append(cluster)
        
        return clusters
    
    @process_statewide_rba.before_loop
    async def before_process_statewide_rba(self):
        await self.bot.wait_until_ready()
    
    async def fetch_statewide_observations(self) -> List[Observation]:
        """Fetch observations for entire Colorado state"""
        try:
            recent_obs_dicts = fetch_ebird_rba("US-CO")
            observations = []
            
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
                    continue

                obs = Observation(
                    checklist_id=d.get("subId"),
                    species=d.get("comName"),
                    region=d.get("subnational2Name"),
                    location=d.get("locName", "Unknown"),
                    observer=d.get("userDisplayName", "Unknown"),
                    obs_datetime=obs_utc,
                    local_tz=tz_name,
                    thread_tracker_key=None,
                    lat=lat,
                    lon=lon,
                    has_media=bool(d.get("hasRichMedia", []))
                )
                
                obs.species_code = d.get("speciesCode")
                observations.append(obs)
            
            return observations
            
        except Exception as e:
            logger.error(f"Error fetching statewide observations: {e}")
            return []
    
    async def process_cluster_for_moderation(self, cluster: List[Observation]):
        """Process a cluster of observations for moderation"""
        try:
            # Use first observation as representative
            representative_obs = cluster[0]
            
            # Skip if already processed
            if is_checklist_rejected(representative_obs.checklist_id, representative_obs.species):
                return
            
            # Check if it's a statewide RBA species
            if not is_species_statewide_rba(normalize_species_name(representative_obs.species)):
                for obs in cluster:
                    mark_checklist_processed_for_moderation(obs.checklist_id)
                return
            
            # Check if nearby thread exists
            if representative_obs.lat and representative_obs.lon:
                nearby_thread = find_nearby_thread(
                    representative_obs.species, 
                    representative_obs.lat, 
                    representative_obs.lon, 
                    4.0
                )
                if nearby_thread:
                    # Add all observations to existing thread
                    for obs in cluster:
                        add_thread_participant(
                            nearby_thread, obs.observer, obs.checklist_id, obs.obs_datetime
                        )
                        mark_checklist_processed_for_moderation(obs.checklist_id)
                    
                    await self.update_thread_recency_for_key(nearby_thread)
                    logger.info(f"Added {len(cluster)} observations to existing thread {nearby_thread}")
                    return
            
            # Needs moderation - add representative to queue
            success = save_pending_checklist(representative_obs)
            if success:
                logger.info(f"Added cluster of {len(cluster)} {representative_obs.species} observations to moderation queue")
            
            # Mark all as processed
            for obs in cluster:
                mark_checklist_processed_for_moderation(obs.checklist_id)
            
        except Exception as e:
            logger.error(f"Error processing cluster for moderation: {e}")
    
    async def process_pending_moderation(self):
        """Send pending moderation items to Discord"""
        try:
            pending_items = get_pending_moderation()
            moderation_channel = self.bot.get_channel(int(self.moderation_channel_id))
            
            if not moderation_channel:
                logger.error("Moderation channel not found")
                return
            
            for item in pending_items:
                if item.get('discord_message_id'):
                    continue
                
                await self.send_moderation_message(moderation_channel, item)
                
        except Exception as e:
            logger.error(f"Error processing pending moderation: {e}")
    
    async def send_moderation_message(self, channel: discord.TextChannel, item: dict):
        """Send a moderation message to Discord with merge candidates"""
        try:
            review_status = get_species_review_status(item['species'])
            if review_status and review_status['is_review_species'] is False and review_status['in_review_list']:
                return
            
            obs_datetime = safe_datetime_parse(item.get('obs_datetime'))
            if obs_datetime is None:
                obs_datetime = datetime.now(timezone.utc)
            
            embed = discord.Embed(
                title=f"{item['species']}, {item['region']}, {obs_datetime.strftime('%b %Y')}",
                color=0xffaa00,
                timestamp=obs_datetime
            )

            observer_link = f"[{item.get('observer', 'Unknown')}](<https://ebird.org/checklist/{item['checklist_id']}>)"
            embed.add_field(name="Observer", value=observer_link, inline=True)
            
            if item.get('lat') and item.get('lon'):
                location_link = f"[{item.get('location', 'Unknown')}](<https://www.google.com/maps/search/?api=1&query={item['lat']},{item['lon']}>)"
                embed.add_field(name="Location", value=location_link, inline=True)
            else:
                embed.add_field(name="Location", value=item.get('location', 'Unknown'), inline=True)
            
            embed.add_field(name="County", value=item.get('region', 'Unknown'), inline=True)
            
            # Get merge candidates
            merge_candidates = []
            if item.get('lat') and item.get('lon'):
                merge_candidates = get_merge_candidates(
                    item['species'], item['lat'], item['lon'], item['region']
                )
            
            view = ModerationView(merge_candidates)
            message = await channel.send(embed=embed, view=view)
            
            # Update database with message ID
            from db import get_connection
            conn = get_connection()
            with conn:
                conn.execute("""
                    UPDATE moderation_queue 
                    SET discord_message_id = ? 
                    WHERE id = ?
                """, (message.id, item['id']))
            
            logger.info(f"Sent moderation message for {item['species']} - {item['checklist_id']}")
            
        except Exception as e:
            logger.error(f"Error sending moderation message: {e}")
    
    @tasks.loop(minutes=10)
    async def update_thread_recency(self):
        """Update recency badges for all active threads"""
        try:
            from db import get_all_threads
            threads = get_all_threads()
            
            for thread in threads:
                await self.update_thread_recency_for_key(thread.tracker_key)
                
        except Exception as e:
            logger.error(f"Error updating thread recency: {e}")
    
    async def update_thread_recency_for_key(self, thread_tracker_key: str):
        """Update recency for a specific thread"""
        try:
            from db import get_checklists_for_thread, save_thread, get_thread
            
            thread = get_thread(thread_tracker_key)
            if not thread:
                return
            
            checklists = get_checklists_for_thread(thread_tracker_key)
            if not checklists:
                return
            
            latest_dt = max(obs.obs_datetime for obs in checklists)
            now_utc = datetime.now(timezone.utc)
            delta = now_utc - latest_dt
            
            if delta < timedelta(hours=24):
                new_bucket = "<24h"
            elif delta < timedelta(days=3):
                new_bucket = "1-3d"
            elif delta < timedelta(days=7):
                new_bucket = "4-7d"
            elif delta < timedelta(days=10):
                new_bucket = "8-10d"
            else:
                new_bucket = ">10d"
            
            if thread.status_bucket != new_bucket:
                thread.status_bucket = new_bucket
                thread.last_seen_at = latest_dt
                save_thread(thread)
                await self.update_discord_thread_title(thread)
                
        except Exception as e:
            logger.error(f"Error updating recency for {thread_tracker_key}: {e}")
    
    async def update_discord_thread_title(self, thread: ThreadRecord):
        """Update Discord thread title with recency badge"""
        try:
            if not hasattr(thread, 'discord_channel_id') or not thread.discord_channel_id:
                return
                
            discord_thread = self.bot.get_channel(thread.discord_channel_id)
            if not discord_thread or not isinstance(discord_thread, discord.Thread):
                return
            
            species = thread.tracker_key.split('|')[0]
            status_emoji, _ = get_status_emoji_and_text(thread.last_seen_at)
            new_name = f"{status_emoji} {species}"
            if discord_thread.name != new_name:
                await discord_thread.edit(name=new_name)
                logger.info(f"Updated thread title: {new_name}")
                
        except Exception as e:
            logger.error(f"Error updating Discord thread title: {e}")

async def setup_moderation_system(bot: commands.Bot, moderation_channel_id: int, statewide_forum_channel_id: int) -> ModerationSystem:
    """Setup and return moderation system"""
    return ModerationSystem(bot, moderation_channel_id, statewide_forum_channel_id)

def get_species_code_from_response(ebird_response_item):
    """Extract species code directly from eBird API response (already includes it with detail=full)"""
    return ebird_response_item.get('speciesCode', '')

def create_map_link(species_code, lat, lon):
    """Create eBird map link for species in area with ±0.025 lat, ±0.03 lon buffer"""
    if not species_code or not lat or not lon:
        return "https://ebird.org/map"
    
    min_lat = lat - 0.025
    max_lat = lat + 0.025
    min_lon = lon - 0.03
    max_lon = lon + 0.03
    
    return f"https://ebird.org/map/{species_code}?env.minX={min_lon}&env.minY={min_lat}&env.maxX={max_lon}&env.maxY={max_lat}&zh=true&yr=cur"

def get_status_emoji_and_text(last_seen_datetime):
    """Get emoji and text for status based on last seen time"""
    now_utc = datetime.now(timezone.utc)
    delta = now_utc - last_seen_datetime
    
    if delta < timedelta(hours=24):
        hours = int(delta.total_seconds() / 3600)
        return "🟢", f"Seen in last {hours} hours" if hours > 1 else "Seen in last hour"
    elif delta < timedelta(days=3):
        return "🟡", f"Seen {delta.days} days ago"
    elif delta < timedelta(days=7):
        return "🟠", f"Seen {delta.days} days ago"
    elif delta < timedelta(days=10):
        return "🔴", f"Seen {delta.days} days ago"
    else:
        return "⚪", f"Seen {delta.days} days ago"

class ThreadStatusView(discord.ui.View):
    """View for thread status and map buttons"""
    
    def __init__(self, species_code, lat, lon):
        super().__init__(timeout=None)
        if lat and lon and species_code:
            map_url = create_map_link(species_code, lat, lon)
            self.add_item(discord.ui.Button(
                label="Map of Recent Reports", 
                style=discord.ButtonStyle.link, 
                url=map_url
            ))

# Updated moderation response functions to add to your existing ModerationView class:

def update_moderation_embed_accepted(embed, moderator):
    """Update embed for accepted moderation"""
    embed.color = 0x00ff00  # Green
    embed.set_footer(text=f"✅ ACCEPTED by {moderator}")
    return embed

def update_moderation_embed_rejected(embed, moderator):
    """Update embed for rejected moderation"""
    embed.color = 0xff0000  # Red
    embed.set_footer(text=f"❌ REJECTED by {moderator}")
    return embed

def create_enhanced_thread_content(mod_item):
    """Create enhanced thread content with your requested format"""
    species = mod_item['species']
    region = mod_item['region']
    location = mod_item.get('location', 'Unknown Location')
    observer = mod_item.get('observer', 'Unknown')
    checklist_id = mod_item['checklist_id']
    lat, lon = mod_item.get('lat'), mod_item.get('lon')
    has_media = mod_item.get('has_media', False)
    
    # Parse datetime (using your existing safe_datetime_parse)
    from moderation import safe_datetime_parse
    from time_utils import ebird_local_to_utc
    
    obs_datetime = safe_datetime_parse(mod_item.get('obs_datetime'))
    if obs_datetime is None:
        obs_datetime = datetime.now(timezone.utc)
    elif obs_datetime.tzinfo is None:
        obs_datetime = ebird_local_to_utc(obs_datetime, lat, lon)
    elif obs_datetime.tzinfo != timezone.utc:
        obs_datetime = obs_datetime.astimezone(timezone.utc)
    
    # Get status emoji and text
    status_emoji, status_text = get_status_emoji_and_text(obs_datetime)
    
    # Create content string in your requested format
    content = f"{status_emoji} {status_text}\n"
    content += f"If you'd like notifications for this report, click the Follow button below.\n"
    content += f"▸ {location}\n"
    content += f"▸ Earliest record by: [**{observer}**](<https://ebird.org/checklist/{checklist_id}>)"
    
    if has_media:
        content += " 📷"
    
    content += f"\n▸ Check the [**eBird Checklist**](<https://ebird.org/checklist/{checklist_id}>) for more details.\n"
    content += f"▸ 1 positive checklists / 0 negative checklists in last 24 hours.\n\n"
    content += f"*Last seen: {obs_datetime.strftime('%Y-%m-%d %H:%M')}* ▸ Reported in last 24 hours by: "
    content += f"[{observer}](<https://ebird.org/checklist/{checklist_id}>)"
    
    if has_media:
        content += " 📷"
    
    # Get species code from mod_item if available (should be there from eBird API)
    species_code = mod_item.get('species_code', '')
    
    # Create view with map button
    view = ThreadStatusView(species_code, lat or 0, lon or 0) if species_code else None
    
    return content, view

def update_thread_title_with_status(thread_name, status_emoji):
    """Update thread title with status emoji for easy scanning"""
    # Remove existing emoji if present
    import re
    clean_name = re.sub(r'^[🟢🟡🟠🔴⚪]\s*', '', thread_name)
    return f"{status_emoji} {clean_name}"