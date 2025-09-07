# COMPREHENSIVE FIXES FOR MODERATION SYSTEM

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
from time import time
import asyncio
import logging
from typing import List, Dict, Optional
import pytz

from db import (
    save_pending_checklist_with_aggregation, get_pending_moderation, update_moderation_status,
    is_checklist_rejected, find_nearby_thread, save_thread, add_thread_participant,
    mark_checklist_processed_for_moderation,
    get_moderation_item_by_message_id, get_checklists_for_thread, get_thread_participants
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
    Returns a datetime object or None if parsing fails.
    """
    if dt_input is None:
        return None
    
    if isinstance(dt_input, datetime):
        return dt_input
    
    if isinstance(dt_input, str):
        try:
            # Try parsing ISO format
            return datetime.fromisoformat(dt_input.replace('Z', '+00:00'))
        except ValueError:
            try:
                # Try parsing eBird format
                return datetime.strptime(dt_input, "%Y-%m-%d %H:%M")
            except ValueError:
                logger.error(f"Could not parse datetime string: {dt_input}")
                return None
    
    if isinstance(dt_input, dict) and 'obs_datetime' in dt_input:
        return safe_datetime_parse(dt_input['obs_datetime'])
    
    logger.error(f"Unexpected datetime input type: {type(dt_input)} - {dt_input}")
    return None

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

def create_map_link(species_code, lat, lon):
    """Create eBird map link for species in area with ±0.025 lat, ±0.03 lon buffer"""
    if not species_code or not lat or not lon:
        return "https://ebird.org/map"
    
    min_lat = lat - 0.025
    max_lat = lat + 0.025
    min_lon = lon - 0.03
    max_lon = lon + 0.03
    
    return f"https://ebird.org/map/{species_code}?env.minX={min_lon}&env.minY={min_lat}&env.maxX={max_lon}&env.maxY={max_lat}&zh=true&yr=cur"

def utc_to_local_time(utc_dt, lat, lon):
    """Convert UTC datetime to local time based on coordinates"""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    
    try:
        tz_name = get_timezone_name(lat, lon)
        local_tz = pytz.timezone(tz_name)
        return utc_dt.astimezone(local_tz)
    except:
        return utc_dt

# DATABASE ADDITIONS NEEDED:

class ThreadStatusView(discord.ui.View):
    """View for thread status and map buttons"""
    
    def __init__(self, species_code, lat, lon, latest_checklist_id):
        super().__init__(timeout=None)
        
        # Add map link button if we have the data
        if lat and lon and species_code:
            map_url = create_map_link(species_code, lat, lon)
            self.add_item(discord.ui.Button(
                label="Map of Recent Reports", 
                style=discord.ButtonStyle.link, 
                url=map_url
            ))
        
        # Add latest checklist button
        if latest_checklist_id:
            checklist_url = f"https://ebird.org/checklist/{latest_checklist_id}"
            self.add_item(discord.ui.Button(
                label="Latest Checklist", 
                style=discord.ButtonStyle.link, 
                url=checklist_url
            ))

class MergeDropdownView(discord.ui.View):
    """View with merge dropdown for nearby threads"""
    
    def __init__(self, merge_candidates: List[dict]):
        super().__init__(timeout=300)  # 5 minute timeout
        
        if merge_candidates:
            options = [
                discord.SelectOption(
                    label=f"{candidate['location']} ({candidate['distance_km']}km)",
                    value=candidate['tracker_key'],
                    description=f"Last seen: {candidate['last_seen'][:10]}"
                )
                for candidate in merge_candidates[:10]  # Discord limit
            ]
            options.append(discord.SelectOption(
                label="Create New Thread",
                value="new_thread",
                description="Create a separate thread for this location"
            ))
            
            self.add_item(MergeSelect(options))

class MergeSelect(discord.ui.Select):
    """Dropdown for selecting merge target"""
    
    def __init__(self, options):
        super().__init__(placeholder="Choose merge target or create new thread...", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        # Handle merge selection (implement based on your needs)
        selected_value = self.values[0]
        if selected_value == "new_thread":
            await interaction.response.send_message("Creating new thread...", ephemeral=True)
        else:
            await interaction.response.send_message(f"Merging with thread: {selected_value}", ephemeral=True)

class ModerationView(discord.ui.View):
    """Persistent view for moderation buttons"""
    
    def __init__(self):
        super().__init__(timeout=None)  # No timeout for persistent views
    
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="mod_accept", emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_moderation(interaction, "accepted")
    
    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="mod_reject", emoji="❌")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_moderation(interaction, "rejected")
    
    async def handle_moderation(self, interaction: discord.Interaction, action: str):
        """Handle accept/reject button clicks"""
        try:
            # Get moderation item from database
            mod_item = get_moderation_item_by_message_id(interaction.message.id)
            if not mod_item:
                await interaction.response.send_message("⚠️ Could not find moderation item.", ephemeral=True)
                return
            
            if mod_item['status'] != 'pending':
                await interaction.response.send_message(
                    f"⚠️ This item has already been {mod_item['status']} by {mod_item['moderated_by']}", 
                    ephemeral=True
                )
                return
            
            moderator = str(interaction.user)
            
            if action == "accepted":
                success = await self.handle_acceptance(mod_item, moderator, interaction)
                if success:
                    # UPDATE: Use embed instead of content
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
                # UPDATE: Use embed instead of content  
                embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
                embed.color = 0xff0000  # Red
                embed.set_footer(text=f"❌ REJECTED by {moderator}")
                await interaction.response.edit_message(embed=embed, view=None)
                logger.info(f"Rejected: {mod_item['species']} from {mod_item['checklist_id']} by {moderator}")
        
        except Exception as e:
            logger.error(f"Error in moderation handling: {e}")
            await interaction.response.send_message("❌ An error occurred.", ephemeral=True)
    
    async def handle_acceptance(self, mod_item: dict, moderator: str, interaction: discord.Interaction) -> bool:
        """Handle acceptance logic - create or merge with thread"""
        try:
            species = mod_item['species']
            lat, lon = mod_item.get('lat'), mod_item.get('lon')
            region = mod_item['region']
            
            logger.debug(f"Processing acceptance for {species}")
            
            # FIX: Parse datetime first, then convert to UTC if needed
            obs_datetime_raw = mod_item.get('obs_datetime')
            obs_datetime = safe_datetime_parse(obs_datetime_raw)
            
            if obs_datetime is None:
                logger.error(f"Could not parse obs_datetime: {obs_datetime_raw}")
                obs_datetime = datetime.now(timezone.utc)
            else:
                # Ensure it's in UTC
                if obs_datetime.tzinfo is None:
                    obs_datetime = ebird_local_to_utc(obs_datetime, lat, lon)
                elif obs_datetime.tzinfo != timezone.utc:
                    obs_datetime = obs_datetime.astimezone(timezone.utc)
            
            # Check for nearby existing thread (2km auto-merge)
            existing_thread_key = None
            if lat is not None and lon is not None:
                existing_thread_key = find_nearby_thread(species, lat, lon, distance_km=2.0)
            
            if existing_thread_key:
                # Auto-merge with existing thread
                thread_tracker_key = existing_thread_key
                logger.info(f"Auto-merging {species} with existing thread {thread_tracker_key}")
                
                # Add participant to thread
                add_thread_participant(
                    thread_tracker_key, 
                    mod_item.get('observer', 'Unknown'), 
                    mod_item['checklist_id'], 
                    obs_datetime
                )
                
                # Update thread content immediately
                await self.update_thread_content(interaction.client, thread_tracker_key)
                
            else:
                # Check for merge candidates (2-10km)
                merge_candidates = get_merge_candidates(species, lat or 0, lon or 0, distance_km=10.0) if lat and lon else []
                
                if merge_candidates:
                    # Show merge dropdown
                    merge_view = MergeDropdownView(merge_candidates)
                    await interaction.response.send_message(
                        f"Found nearby threads for {species}. Choose merge target:", 
                        view=merge_view, 
                        ephemeral=True
                    )
                    return True  # Don't create thread yet, wait for user choice
                else:
                    # Create new thread
                    # FIX: Use correct tracker key format
                    thread_tracker_key = f"{species}|{region}|{obs_datetime.strftime('%b %Y')}"
                    
                    # Create thread in statewide forum
                    statewide_forum_channel_id = getattr(interaction.client, 'statewide_forum_channel_id', None)
                    if statewide_forum_channel_id:
                        forum_channel = interaction.guild.get_channel(int(statewide_forum_channel_id))
                        if forum_channel and isinstance(forum_channel, discord.ForumChannel):
                            # Create thread in forum
                            thread_name = f"{species}, {region}, {obs_datetime.strftime('%b %Y')}"
                            
                            # Create enhanced thread content
                            content, view = await self.create_enhanced_thread_content(mod_item, thread_tracker_key)
                            
                            thread = await forum_channel.create_thread(
                                name=thread_name,
                                content=content,
                                view=view
                            )
                            
                            # Save thread to database
                            thread_record = ThreadRecord(
                                tracker_key=thread_tracker_key,
                                thread_id=thread.thread.id,
                                type="bot",
                                last_seen_at=obs_datetime,
                                status_bucket="<24h"
                            )
                            thread_record.discord_channel_id = thread.thread.id
                            thread_record.discord_message_id = thread.message.id
                            save_thread(thread_record)
                            
                            # Add participant to new thread
                            add_thread_participant(
                                thread_tracker_key, 
                                mod_item.get('observer', 'Unknown'), 
                                mod_item['checklist_id'], 
                                obs_datetime
                            )
                            
                            logger.info(f"Created new thread {thread_tracker_key} in forum")
            
            # Update moderation status
            update_moderation_status(
                mod_item['checklist_id'], 'accepted', moderator, species=mod_item['species']
            )
            
            logger.info(f"Accepted: {species} from {mod_item['checklist_id']} by {moderator}")
            return True
            
        except Exception as e:
            logger.error(f"Error in acceptance handling: {e}")
            logger.error(f"mod_item: {mod_item}")
            return False

    async def create_enhanced_thread_content(self, mod_item: dict, thread_tracker_key: str):
        """Create enhanced thread content with proper formatting"""
        species = mod_item['species']
        region = mod_item['region']
        location = mod_item.get('location', 'Unknown Location')
        observer = mod_item.get('observer', 'Unknown')
        checklist_id = mod_item['checklist_id']
        lat, lon = mod_item.get('lat'), mod_item.get('lon')
        has_media = mod_item.get('has_media', False)
        species_code = mod_item.get('species_code', '')
        
        # Parse datetime
        obs_datetime = safe_datetime_parse(mod_item.get('obs_datetime'))
        if obs_datetime is None:
            obs_datetime = datetime.now(timezone.utc)
        elif obs_datetime.tzinfo is None:
            obs_datetime = ebird_local_to_utc(obs_datetime, lat, lon)
        elif obs_datetime.tzinfo != timezone.utc:
            obs_datetime = obs_datetime.astimezone(timezone.utc)
        
        # Convert to local time for display
        local_datetime = utc_to_local_time(obs_datetime, lat, lon) if lat and lon else obs_datetime
        
        # Get status emoji and text
        status_emoji, status_text = get_status_emoji_and_text(obs_datetime)
        
        # Create content string in requested format
        content = f"**{species}, {region}, {local_datetime.strftime('%b %Y')}**\n\n"
        content += f"{status_emoji} {status_text}\n"
        content += f"If you'd like notifications for this report, click the Follow button below.\n"
        content += f"▸ {location}\n"
        content += f"▸ Earliest record by: [**{observer}**](<https://ebird.org/checklist/{checklist_id}>)"
        
        if has_media:
            content += " 📷"
        
        content += f"\n▸ Check the [**eBird Checklist**](<https://ebird.org/checklist/{checklist_id}>) for more details.\n"
        content += f"▸ 1 positive checklists / 0 negative checklists in last 24 hours.\n\n"
        content += f"▸ Reported in last 24 hours by: [{observer}](<https://ebird.org/checklist/{checklist_id}>)"
        
        if has_media:
            content += " 📷"
        
        content += f"\n\n*Last seen: {local_datetime.strftime('%Y-%m-%d %H:%M')}*"
        
        # Create view with buttons
        view = ThreadStatusView(species_code, lat, lon, checklist_id)
        
        return content, view

    async def update_thread_content(self, bot, thread_tracker_key: str):
        """Update existing thread content with new data"""
        try:
            from db import get_thread
            thread = get_thread(thread_tracker_key)
            if not thread or not hasattr(thread, 'discord_message_id') or not thread.discord_message_id:
                return
            
            # Get the thread channel and message
            if hasattr(thread, 'discord_channel_id') and thread.discord_channel_id:
                channel = bot.get_channel(thread.discord_channel_id)
                if channel:
                    try:
                        message = await channel.fetch_message(thread.discord_message_id)
                        
                        # Get all checklists for this thread
                        checklists = get_checklists_for_thread(thread_tracker_key)
                        if not checklists:
                            return
                        
                        # Update content with current data
                        await self.refresh_thread_content(message, thread_tracker_key, checklists)
                        
                    except discord.NotFound:
                        logger.warning(f"Message {thread.discord_message_id} not found for thread {thread_tracker_key}")
                    except Exception as e:
                        logger.error(f"Error updating thread message: {e}")
        except Exception as e:
            logger.error(f"Error in update_thread_content: {e}")

    async def refresh_thread_content(self, message: discord.Message, thread_tracker_key: str, checklists: List[Observation]):
        """Refresh thread content with updated statistics"""
        try:
            if not checklists:
                return
            
            # Get the first checklist for basic info
            first_obs = checklists[0]
            species = first_obs.species
            region = first_obs.region
            
            # Calculate statistics
            now_utc = datetime.now(timezone.utc)
            recent_cutoff = now_utc - timedelta(hours=24)
            
            recent_reports = [obs for obs in checklists if obs.obs_datetime >= recent_cutoff]
            latest_obs = max(checklists, key=lambda x: x.obs_datetime)
            
            # Get status emoji and text based on latest observation
            status_emoji, status_text = get_status_emoji_and_text(latest_obs.obs_datetime)
            
            # Convert to local time for display
            local_datetime = utc_to_local_time(latest_obs.obs_datetime, latest_obs.lat, latest_obs.lon) if latest_obs.lat and latest_obs.lon else latest_obs.obs_datetime
            
            # Build content with your updated format
            content = f"**{species}, {region}, {local_datetime.strftime('%b %Y')}**\n\n"
            content += f"{status_emoji} {status_text}\n"
            content += f"If you'd like notifications for this report, click the Follow button below.\n"
            content += f"▸ {first_obs.location}\n"
            content += f"▸ Earliest record by: [**{first_obs.observer}**](<https://ebird.org/checklist/{first_obs.checklist_id}>)"
            
            if first_obs.has_media:
                content += " 📷"
            
            content += f"\n▸ Check the [**eBird Checklist**](<https://ebird.org/checklist/{first_obs.checklist_id}>) for more details.\n"
            content += f"▸ {len(recent_reports)} positive checklists / 0 negative checklists in last 24 hours.\n\n"
            content += f"▸ Reported in last 24 hours by: "
            
            # List recent reporters
            recent_reporter_links = []
            for obs in recent_reports[-8:]:  # Limit to last 8 reports
                link = f"[{obs.observer}](<https://ebird.org/checklist/{obs.checklist_id}>)"
                if obs.has_media:
                    link += " 📷"
                recent_reporter_links.append(link)
            
            content += ", ".join(recent_reporter_links)
            content += f"\n\n*Last seen: {local_datetime.strftime('%Y-%m-%d %H:%M')}*"
            
            # Update view with latest checklist
            view = ThreadStatusView(
                getattr(first_obs, 'species_code', ''), 
                first_obs.lat, 
                first_obs.lon, 
                latest_obs.checklist_id
            )
            
            await message.edit(content=content, view=view)
            
        except Exception as e:
            logger.error(f"Error refreshing thread content: {e}")

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
            
            # Fetch statewide data
            statewide_obs = await self.fetch_statewide_observations()
            
            # Process each observation
            for obs in statewide_obs:
                await self.process_observation_for_moderation(obs)
                
            # Process pending moderation queue
            await self.process_pending_moderation()
            
            logger.info("Completed statewide RBA processing")
            
        except Exception as e:
            logger.error(f"Error in statewide RBA processing: {e}")
    
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
                    continue  # Skip malformed dates

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
                
                # Add species code if available
                obs.species_code = d.get("speciesCode")
                observations.append(obs)
            
            return observations
            
        except Exception as e:
            logger.error(f"Error fetching statewide observations: {e}")
            return []
    
    # async def process_observation_for_moderation(self, obs: Observation):
    #     """Process a single observation to see if it needs moderation"""
    #     try:
    #         # Skip if already processed
    #         if is_checklist_rejected(obs.checklist_id, obs.species):
    #             return
            
    #         # Check if it's a statewide RBA species using CO Review List
    #         if not is_species_statewide_rba(normalize_species_name(obs.species)):
    #             mark_checklist_processed_for_moderation(obs.checklist_id)
    #             return
            
    async def process_observation_for_moderation(self, obs: Observation):
        """Process a single observation to see if it needs moderation"""
        try:
            # Skip if already processed
            if is_checklist_rejected(obs.checklist_id, obs.species):
                return
            
            # Check if it's a statewide RBA species using CO Review List
            if not is_species_statewide_rba(normalize_species_name(obs.species)):
                mark_checklist_processed_for_moderation(obs.checklist_id)
                return
            
            # Check if nearby thread exists (within 2km) - auto-merge
            if obs.lat is not None and obs.lon is not None:
                nearby_thread = find_nearby_thread(obs.species, obs.lat, obs.lon, 2.0)
                if nearby_thread:
                    # Add to existing thread without moderation
                    add_thread_participant(
                        nearby_thread, obs.observer, obs.checklist_id, obs.obs_datetime
                    )
                    mark_checklist_processed_for_moderation(obs.checklist_id)
                    await self.update_thread_recency_for_key(nearby_thread)
                    logger.info(f"Auto-merged {obs.species} to existing thread {nearby_thread}")
                    return
            
            # Use aggregation logic for moderation queue
            success = save_pending_checklist_with_aggregation(obs)
            if success:
                logger.info(f"Added {obs.species} from {obs.checklist_id} to moderation queue")
            else:
                logger.info(f"Skipped {obs.species} from {obs.checklist_id} - too close to existing pending")
            
            mark_checklist_processed_for_moderation(obs.checklist_id)
            
        except Exception as e:
            logger.error(f"Error processing observation for moderation: {e}")
    
    async def process_pending_moderation(self):
        """Send pending moderation items to Discord"""
        try:
            pending_items = get_pending_moderation()
            moderation_channel = self.bot.get_channel(int(self.moderation_channel_id))
            
            if not moderation_channel:
                logger.error("Moderation channel not found")
                return
            
            for item in pending_items:
                # Skip if already has Discord message
                if item['discord_message_id']:
                    continue
                
                await self.send_moderation_message(moderation_channel, item)
                
        except Exception as e:
            logger.error(f"Error processing pending moderation: {e}")
    
    async def send_moderation_message(self, channel: discord.TextChannel, item: dict):
        """Send a moderation message to Discord"""
        try:
            review_status = get_species_review_status(item['species'])
            if review_status['is_review_species'] is False and review_status['in_review_list']:
                return  # No moderation needed
            
            # Safely parse datetime for embed
            obs_datetime = safe_datetime_parse(item.get('obs_datetime'))
            if obs_datetime is None:
                obs_datetime = datetime.now(timezone.utc)
            
            # Include has_media info for camera emoji
            has_media = item.get('has_media', False)
            
            embed = discord.Embed(
                title=f"{item['species']}, {item['region']}, {obs_datetime.strftime('%b %Y')}",
                color=0xffaa00,
                timestamp=obs_datetime
            )
            
            observer_text = f"[{item.get('observer', 'Unknown')}](<https://ebird.org/checklist/{item['checklist_id']}>)"
            if has_media:
                observer_text += " 📷"
            
            embed.add_field(name="Observer", value=observer_text, inline=True)
            embed.add_field(name="Location", value=f"[{item.get('location', 'Unknown')}](https://www.google.com/maps/search/?api=1&query={item['lat']},{item['lon']})", inline=True)
            embed.add_field(name="County", value=item.get('region', 'Unknown'), inline=True)
            
            view = ModerationView()
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
            from db import get_thread
            
            # Get thread and checklists
            thread = get_thread(thread_tracker_key)
            if not thread:
                return
            
            checklists = get_checklists_for_thread(thread_tracker_key)
            if not checklists:
                return
            
            # Calculate new recency
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
            
            # Update if changed
            if thread.status_bucket != new_bucket:
                thread.status_bucket = new_bucket
                thread.last_seen_at = latest_dt
                save_thread(thread)
                
                # Update Discord thread title if possible
                await self.update_discord_thread_title(thread)
                
            # CRITICAL: Update thread content every 10 minutes
            moderation_view = ModerationView()
            await moderation_view.update_thread_content(self.bot, thread_tracker_key)
                
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
            
            # Extract species name from tracker key
            species = thread.tracker_key.split('|')[0]
            
            # Use emoji instead of text buckets for easy scanning
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