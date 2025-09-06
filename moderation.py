# moderation.py - Complete moderation system for statewide RBAs
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
from time import time
import asyncio
import logging
from typing import List, Dict, Optional
from dotenv import load_dotenv
import os

from db import (
    save_pending_checklist, get_pending_moderation, update_moderation_status,
    is_checklist_rejected, find_nearby_thread, save_thread, add_thread_participant,
    get_thread_participants, mark_checklist_processed_for_moderation,
    get_unprocessed_checklists_for_moderation, get_moderation_item_by_message_id
)
from co_review_loader import get_species_review_status, is_species_statewide_rba
from models import Observation, ThreadRecord
from ebird_api import fetch_ebird_rba
from time_utils import ebird_local_to_utc, get_timezone_name
from geo_utils import haversine, normalize_species_name

logger = logging.getLogger("Dipper_RBA_Bot")

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
                    await interaction.response.edit_message(
                        content=f"✅ **ACCEPTED** by {moderator}\n\n" + interaction.message.content,
                        view=None  # Remove buttons
                    )
                else:
                    await interaction.response.send_message("❌ Error processing acceptance.", ephemeral=True)
            
            elif action == "rejected":
                update_moderation_status(
                    mod_item['checklist_id'], mod_item['species'], 
                    'rejected', moderator
                )
                await interaction.response.edit_message(
                    content=f"❌ **REJECTED** by {moderator}\n\n" + interaction.message.content,
                    view=None
                )
                logger.info(f"Rejected: {mod_item['species']} from {mod_item['checklist_id']} by {moderator}")
        
        except Exception as e:
            logger.error(f"Error in moderation handling: {e}")
            await interaction.response.send_message("❌ An error occurred.", ephemeral=True)
    
    async def handle_acceptance(self, mod_item: dict, moderator: str, interaction: discord.Interaction) -> bool:
        """Handle acceptance logic - create or merge with thread"""
        try:
            species = mod_item['species']
            lat, lon = mod_item['lat'], mod_item['lon']
            region = mod_item['region']
            obs_datetime = datetime.fromisoformat(mod_item['obs_datetime'])
            logger.debug(f"Obs datetime: {type(obs_datetime)}")

            # Check for nearby existing thread
            existing_thread_key = None
            if lat is not None and lon is not None:
                existing_thread_key = find_nearby_thread(species, lat, lon, distance_km=4.0)
            
            if existing_thread_key:
                # Merge with existing thread
                thread_tracker_key = existing_thread_key
                logger.info(f"Merging {species} with existing thread {thread_tracker_key}")
            else:
                # Create new thread
                thread_tracker_key = f"{species}, {region}, {obs_datetime.strftime('%b %Y')}"
                
                # Create thread in statewide forum
                forum_channel = interaction.guild.get_channel(STATEWIDE_FORUM_CHANNEL_ID)
                if forum_channel and isinstance(forum_channel, discord.ForumChannel):
                    # Create thread in forum
                    location_str = mod_item.get('location', 'Unknown Location')
                    thread_name = f"{species} - {location_str}"
                    
                    # Create initial embed for the thread
                    embed = await create_thread_embed(mod_item)
                    
                    thread = await forum_channel.create_thread(
                        name=thread_name,
                        content=f"**{species}** sighting accepted for statewide tracking",
                        embed=embed
                    )
                    
                    # Save thread to database
                    thread_record = ThreadRecord(
                        tracker_key=thread_tracker_key,
                        thread_id=thread.thread.id,
                        type="bot",
                        last_seen_at=obs_datetime.strftime("%Y-%m-%d %H:%M"),
                        status_bucket="<24h"
                    )
                    thread_record.discord_channel_id = thread.thread.id
                    save_thread(thread_record)
                    
                    logger.info(f"Created new thread {thread_tracker_key} in forum")
            
            # Add participant to thread
            add_thread_participant(
                thread_tracker_key, 
                mod_item['observer'], 
                mod_item['checklist_id'], 
                obs_datetime.strftime("%Y-%m-%d %H:%M")
            )
            
            # Update moderation status
            update_moderation_status(
                mod_item['checklist_id'], mod_item['species'],
                'accepted', moderator, thread_tracker_key
            )
            
            logger.info(f"Accepted: {species} from {mod_item['checklist_id']} by {moderator}")
            return True
            
        except Exception as e:
            logger.error(f"Error in acceptance handling: {e}")
            return False

async def create_thread_embed(mod_item: dict) -> discord.Embed:
    """Create embed for thread post"""
    embed = discord.Embed(
        title=f"🦅 {mod_item['species']}",
        color=0x00ff00,
        timestamp=mod_item['obs_datetime'].strftime("%Y-%m-%d %H:%M")
    )
    
    embed.add_field(name="Location", value=mod_item.get('location', 'Unknown'), inline=True)
    embed.add_field(name="Observer", value=mod_item.get('observer', 'Unknown'), inline=True)
    embed.add_field(name="Region", value=mod_item.get('region', 'Unknown'), inline=True)
    
    if mod_item.get('lat') and mod_item.get('lon'):
        maps_url = f"https://www.google.com/maps/search/?api=1&query={mod_item['lat']},{mod_item['lon']}"
        embed.add_field(name="📍 Map", value=f"[View Location]({maps_url})", inline=False)
    
    checklist_url = f"https://ebird.org/checklist/{mod_item['checklist_id']}"
    embed.add_field(name="🔗 eBird", value=f"[View Checklist]({checklist_url})", inline=False)
    
    return embed

class ModerationSystem:
    """Main moderation system class"""
    
    def __init__(self, bot: commands.Bot, moderation_channel_id: int, statewide_forum_channel_id: int):
        self.bot = bot
        self.moderation_channel_id = moderation_channel_id
        self.statewide_forum_channel_id = statewide_forum_channel_id
        
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
    
    async def process_observation_for_moderation(self, obs: Observation):
        """Process a single observation to see if it needs moderation"""
        try:
            # Skip if already processed
            if is_checklist_rejected(obs.checklist_id, obs.species):
                return
            
            # Check if it's a statewide RBA species
            species = normalize_species_name(obs.species)
            # print(f"Processing {obs.species}")
            # print(f"Normalized species: {species}")
            if not species or not is_species_statewide_rba(species):
                mark_checklist_processed_for_moderation(obs.checklist_id)
                return
            
            # Check if nearby thread exists (within 4km) - if so, just add to thread
            if obs.lat is not None and obs.lon is not None:
                nearby_thread = find_nearby_thread(obs.species, obs.lat, obs.lon, 4.0)
                if nearby_thread:
                    # Add to existing thread without moderation
                    add_thread_participant(
                        nearby_thread, obs.observer, obs.checklist_id, obs.obs_datetime
                    )
                    mark_checklist_processed_for_moderation(obs.checklist_id)
                    await self.update_thread_recency_for_key(nearby_thread)
                    logger.info(f"Added {obs.species} to existing thread {nearby_thread}")
                    return
            
            # Needs moderation - add to queue
            success = save_pending_checklist(obs)
            if success:
                logger.info(f"Added {obs.species} from {obs.checklist_id} to moderation queue")
            
            mark_checklist_processed_for_moderation(obs.checklist_id)
            
        except Exception as e:
            logger.error(f"Error processing observation for moderation: {e}")
    
    async def process_pending_moderation(self):
        """Send pending moderation items to Discord"""
        try:
            pending_items = get_pending_moderation()
            print(f"Pending moderation items: {len(pending_items)}")
            print(f"Moderation channel ID: {self.moderation_channel_id}")
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
            # Check why it needs moderation
            review_status = get_species_review_status(normalize_species_name(item['species']))
            # print(f"\n\n{item['species']} is a {item['is_review_species']} review species")
            print(f"review_status: {review_status}")
            if review_status == 'in_review_list':
                return
        
            print(item)
            obs_datetime = datetime.fromisoformat(item['obs_datetime'])
            embed = discord.Embed(
                title=f"{item['species']}, {item['region']}, {obs_datetime.strftime('%b %Y')}",
                color=0xffaa00,
                timestamp=datetime.fromisoformat(item['obs_datetime'])
            )

            embed.add_field(name="Observer", value=f"[{item.get('observer', 'Unknown')}](https://ebird.org/checklist/{item['checklist_id']})", inline=True)
            embed.add_field(name="Location", value=f"[{item.get('location', 'Unknown')}](https://www.google.com/maps/search/?api=1&query={item['lat']},{item['lon']})", inline=True)
            embed.add_field(name="Region", value=item.get('region', 'Unknown'), inline=True)
            
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
            from db import get_checklists_for_thread, save_thread, get_thread
            
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
            
            # Update title with recency badge
            new_name = f"[{thread.status_bucket}] {species}"
            if discord_thread.name != new_name:
                await discord_thread.edit(name=new_name)
                logger.info(f"Updated thread title: {new_name}")
                
        except Exception as e:
            logger.error(f"Error updating Discord thread title: {e}")

# # Global constants - set these in your bot_main.py
MODERATION_CHANNEL_ID = os.getenv("MODERATION_CHANNEL_ID")
STATEWIDE_FORUM_CHANNEL_ID = os.getenv("STATEWIDE_FORUM_CHANNEL_ID")

async def setup_moderation_system(bot: commands.Bot, moderation_channel_id: int, statewide_forum_channel_id: int) -> ModerationSystem:
    """Setup and return moderation system"""
    return ModerationSystem(bot, moderation_channel_id, statewide_forum_channel_id)