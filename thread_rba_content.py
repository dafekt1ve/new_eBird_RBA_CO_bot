# thread_rba_content.py
import discord
from datetime import datetime
from typing import List, Optional, Dict
from models import ModerationEntry, ModerationStatus
from moderation_utils import (
    update_moderation_entry_status, 
    update_discord_message_id,
    get_moderation_entry
)
from clustering_utils import (
    cluster_moderation_entries, 
    get_cluster_representative,
    should_cluster_for_moderation
)
from db import save_checklist, save_thread
from models import Observation, ThreadRecord, ThreadType
from rba_formatter import chunked_rba_messages
import logging

logger = logging.getLogger("Dipper_RBA_Bot")

def create_thread_message(observations: List[Observation]) -> str:
    """Create a formatted message for a forum thread from observations."""
    if not observations:
        return "No observations available."
    
    # Use the existing chunked_rba_messages function
    messages = chunked_rba_messages(observations)
    
    # Join all messages with double newline
    return "\n\n".join(messages)

class ModerationView(discord.ui.View):
    """Discord View for moderation Accept/Reject buttons."""
    
    def __init__(self, checklist_id: str = "", species: str = ""):
        super().__init__(timeout=None)  # No timeout since we don't want buttons to expire
        self.checklist_id = checklist_id
        self.species = species

    @discord.ui.button(label='Accept', style=discord.ButtonStyle.green, emoji='✅', custom_id='moderation_accept')
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Extract checklist_id and species from message embed if not set
        if not self.checklist_id or not self.species:
            await self._extract_moderation_data_from_message(interaction.message)
        
        await self.handle_moderation_action(interaction, ModerationStatus.ACCEPTED)

    @discord.ui.button(label='Reject', style=discord.ButtonStyle.red, emoji='❌', custom_id='moderation_reject')
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Extract checklist_id and species from message embed if not set
        if not self.checklist_id or not self.species:
            await self._extract_moderation_data_from_message(interaction.message)
            
        await self.handle_moderation_action(interaction, ModerationStatus.REJECTED)

    async def _extract_moderation_data_from_message(self, message: discord.Message):
        """Extract checklist_id and species from the message embed."""
        try:
            if message.embeds:
                embed = message.embeds[0]
                
                # Extract species from description
                if embed.description:
                    # Remove markdown formatting
                    species = embed.description.replace('**', '').strip()
                    self.species = species
                
                # Extract checklist_id from embed fields
                for field in embed.fields:
                    if field.name == "Checklist ID":
                        self.checklist_id = field.value
                        break
                        
                logger.info(f"Extracted moderation data: {self.species} - {self.checklist_id}")
        except Exception as e:
            logger.error(f"Failed to extract moderation data from message: {e}")


class ClusterModerationView(discord.ui.View):
    """Discord View for cluster moderation with Accept All/Reject All buttons."""
    
    def __init__(self, cluster_id: str = "", entries: List[ModerationEntry] = None):
        super().__init__(timeout=None)
        self.cluster_id = cluster_id
        self.entries = entries or []
        self.species = entries[0].species if entries else "Unknown"

    @discord.ui.button(label='Accept All', style=discord.ButtonStyle.green, emoji='✅', custom_id='cluster_accept_all')
    async def accept_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # For cluster views, we'd need to reconstruct the entries from the database
        # This is more complex and might require storing cluster info differently
        await interaction.response.send_message("Cluster moderation after bot restart not yet implemented.", ephemeral=True)

    @discord.ui.button(label='Reject All', style=discord.ButtonStyle.red, emoji='❌', custom_id='cluster_reject_all')
    async def reject_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # For cluster views, we'd need to reconstruct the entries from the database
        await interaction.response.send_message("Cluster moderation after bot restart not yet implemented.", ephemeral=True)

    async def handle_moderation_action(self, interaction: discord.Interaction, action: ModerationStatus):
        """Handle accept/reject button clicks."""
        
        # Check if user has moderator role
        if not any(role.name.lower() == 'moderator' for role in interaction.user.roles):
            await interaction.response.send_message("You don't have permission to moderate.", ephemeral=True)
            return

        try:
            # Get the moderation entry
            entry = get_moderation_entry(self.checklist_id, self.species)
            if not entry:
                await interaction.response.send_message("Moderation entry not found.", ephemeral=True)
                return
                
            if entry.status != ModerationStatus.PENDING:
                await interaction.response.send_message(
                    f"This item has already been {entry.status.value} by {entry.moderated_by}.", 
                    ephemeral=True
                )
                return

            # Update the moderation status
            success = update_moderation_entry_status(
                self.checklist_id, 
                self.species, 
                action, 
                str(interaction.user.id),
                datetime.now(),
                str(interaction.message.id)
            )

            if not success:
                await interaction.response.send_message("Failed to update moderation status.", ephemeral=True)
                return

            # Disable buttons and update message
            for item in self.children:
                item.disabled = True

            action_word = "accepted" if action == ModerationStatus.ACCEPTED else "rejected"
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green() if action == ModerationStatus.ACCEPTED else discord.Color.red()
            embed.add_field(
                name="Moderation Decision", 
                value=f"{action_word.title()} by {interaction.user.mention} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )

            await interaction.response.edit_message(embed=embed, view=self)

            # If accepted, create thread and save to database
            if action == ModerationStatus.ACCEPTED:
                await self.handle_acceptance(entry, interaction)
                
            logger.info(f"Moderation {action.value}: {self.species} in {self.checklist_id} by {interaction.user.name}")

        except Exception as e:
            logger.error(f"Error handling moderation action: {e}")
            await interaction.response.send_message("An error occurred while processing your request.", ephemeral=True)

    async def handle_acceptance(self, entry: ModerationEntry, interaction: discord.Interaction):
        """Handle the acceptance workflow: save to DB and create thread."""
        try:
            # Convert ModerationEntry to Observation for database storage
            obs = Observation(
                checklist_id=entry.checklist_id,
                species=entry.species,
                subspecies=None,  # We might need to extract this if available
                region=entry.region,
                location=entry.location or "Unknown",
                observer=entry.observer or "Unknown",
                obs_datetime=entry.obs_datetime,
                local_tz=entry.local_tz,
                thread_tracker_key=f"{entry.species}|{entry.region}",
                lat=entry.lat,
                lon=entry.lon,
                counted=False,
                has_media=entry.has_media
            )

            # Save to checklists database
            save_checklist(obs)

            # Create or update thread
            await self.create_forum_thread(obs, interaction)

        except Exception as e:
            logger.error(f"Error handling acceptance: {e}")
            # Still count as accepted in moderation, but log the error

    async def create_forum_thread(self, obs: Observation, interaction: discord.Interaction):
        """Create a new thread in the forum for the accepted observation."""
        try:
            # Find the co-statewide-RBA forum channel
            guild = interaction.guild
            forum_channel = discord.utils.get(guild.channels, name="co-statewide-rba")
            
            if not forum_channel or not isinstance(forum_channel, discord.ForumChannel):
                logger.error("Could not find co-statewide-rba forum channel")
                return

            # Create thread title and content
            thread_title = f"{obs.species} - {obs.location}"
            thread_content = create_thread_message([obs])

            # Create the thread
            thread = await forum_channel.create_thread(
                name=thread_title,
                content=thread_content
            )

            # Save thread to database
            thread_record = ThreadRecord(
                tracker_key=obs.thread_tracker_key,
                thread_id=thread.id,
                type=ThreadType.BOT,
                last_seen_at=datetime.now(),
                status_bucket="<24h"
            )
            save_thread(thread_record)

            logger.info(f"Created forum thread for {obs.species}: {thread.jump_url}")

        except Exception as e:
            logger.error(f"Error creating forum thread: {e}")


class ClusterModerationView(discord.ui.View):
    """Discord View for cluster moderation with Accept All/Reject All buttons."""
    
    def __init__(self, cluster_id: str, entries: List[ModerationEntry]):
        super().__init__(timeout=None)
        self.cluster_id = cluster_id
        self.entries = entries
        self.species = entries[0].species if entries else "Unknown"

    @discord.ui.button(label='Accept All', style=discord.ButtonStyle.green, emoji='✅', custom_id='cluster_accept_all')
    async def accept_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_cluster_moderation(interaction, ModerationStatus.ACCEPTED)

    @discord.ui.button(label='Reject All', style=discord.ButtonStyle.red, emoji='❌', custom_id='cluster_reject_all')
    async def reject_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_cluster_moderation(interaction, ModerationStatus.REJECTED)

    async def handle_cluster_moderation(self, interaction: discord.Interaction, action: ModerationStatus):
        """Handle cluster-wide accept/reject decisions."""
        
        # Check if user has moderator role
        if not any(role.name.lower() == 'moderator' for role in interaction.user.roles):
            await interaction.response.send_message("You don't have permission to moderate.", ephemeral=True)
            return

        try:
            processed_count = 0
            failed_count = 0
            
            for entry in self.entries:
                # Skip if already processed
                if entry.status != ModerationStatus.PENDING:
                    continue
                
                # Update the moderation status
                success = update_moderation_entry_status(
                    entry.checklist_id, 
                    entry.species, 
                    action, 
                    str(interaction.user.id),
                    datetime.now(),
                    str(interaction.message.id)
                )
                
                if success:
                    processed_count += 1
                    # If accepted, handle the acceptance workflow
                    if action == ModerationStatus.ACCEPTED:
                        await self.handle_entry_acceptance(entry, interaction)
                else:
                    failed_count += 1

            # Disable buttons and update message
            for item in self.children:
                item.disabled = True

            action_word = "accepted" if action == ModerationStatus.ACCEPTED else "rejected"
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green() if action == ModerationStatus.ACCEPTED else discord.Color.red()
            
            status_text = f"Cluster {action_word} by {interaction.user.mention} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            if failed_count > 0:
                status_text += f"\n⚠️ {failed_count} entries failed to update"
            
            embed.add_field(name="Moderation Decision", value=status_text, inline=False)

            await interaction.response.edit_message(embed=embed, view=self)
            
            logger.info(f"Cluster moderation {action.value}: {self.species} cluster {self.cluster_id} - {processed_count} processed, {failed_count} failed")

        except Exception as e:
            logger.error(f"Error handling cluster moderation: {e}")
            await interaction.response.send_message("An error occurred while processing the cluster.", ephemeral=True)

    async def handle_entry_acceptance(self, entry: ModerationEntry, interaction: discord.Interaction):
        """Handle acceptance for a single entry in the cluster."""
        try:
            # Convert to observation and save
            obs = Observation(
                checklist_id=entry.checklist_id,
                species=entry.species,
                subspecies=None,
                region=entry.region,
                location=entry.location or "Unknown",
                observer=entry.observer or "Unknown",
                obs_datetime=entry.obs_datetime,
                local_tz=entry.local_tz,
                thread_tracker_key=f"{entry.species}|{entry.region}",
                lat=entry.lat,
                lon=entry.lon,
                counted=False,
                has_media=entry.has_media
            )
            
            save_checklist(obs)
            
        except Exception as e:
            logger.error(f"Error handling entry acceptance in cluster: {e}")


def create_moderation_embed(entry: ModerationEntry) -> discord.Embed:
    """Create a Discord embed for a single moderation entry."""
    
    # Create embed with species info
    embed = discord.Embed(
        title=f"🔍 Species Review Required",
        description=f"**{entry.species}**",
        color=discord.Color.yellow(),
        timestamp=entry.submitted_at
    )

    # Add checklist information
    embed.add_field(name="Observer", value=entry.observer or "Unknown", inline=True)
    embed.add_field(name="Location", value=entry.location or "Unknown", inline=True)
    embed.add_field(name="Region", value=entry.region, inline=True)

    # Add observation details
    obs_time_str = entry.obs_datetime.strftime("%Y-%m-%d %H:%M")
    embed.add_field(name="Date/Time", value=f"{obs_time_str} ({entry.local_tz})", inline=True)
    embed.add_field(name="Has Media", value="Yes" if entry.has_media else "No", inline=True)
    embed.add_field(name="Checklist ID", value=entry.checklist_id, inline=True)

    # Add eBird link
    ebird_url = f"https://ebird.org/checklist/{entry.checklist_id}"
    embed.add_field(name="eBird Link", value=f"[View Checklist]({ebird_url})", inline=False)

    # Add footer
    embed.set_footer(text="Select Accept to add to RBA or Reject to dismiss")

    return embed


def create_cluster_moderation_embed(cluster_id: str, entries: List[ModerationEntry]) -> discord.Embed:
    """Create a Discord embed for clustered moderation entries."""
    
    if not entries:
        raise ValueError("entries list cannot be empty")
    
    representative = entries[0]  # Should use clustering logic to get best representative
    species = representative.species
    
    # Create embed with cluster info
    embed = discord.Embed(
        title=f"🔍 Clustered Species Review",
        description=f"**{species}** ({len(entries)} reports)",
        color=discord.Color.orange(),
        timestamp=representative.submitted_at
    )

    # Add cluster summary
    locations = list(set([e.location for e in entries if e.location]))[:3]
    location_text = ", ".join(locations[:2])
    if len(locations) > 2:
        location_text += f", and {len(locations)-2} more"
    
    embed.add_field(name="Locations", value=location_text or "Various", inline=True)
    embed.add_field(name="Reports", value=str(len(entries)), inline=True)
    embed.add_field(name="Region", value=representative.region, inline=True)

    # Add representative observation details
    obs_time_str = representative.obs_datetime.strftime("%Y-%m-%d %H:%M")
    embed.add_field(name="First Report", value=f"{obs_time_str} by {representative.observer}", inline=False)
    
    # Add map link if coordinates available
    if representative.lat and representative.lon:
        map_url = f"https://www.google.com/maps/search/?api=1&query={representative.lat},{representative.lon}"
        embed.add_field(name="Location", value=f"[View on Map]({map_url})", inline=True)

    # Add media indicator
    media_count = sum(1 for e in entries if e.has_media)
    if media_count > 0:
        embed.add_field(name="Media", value=f"{media_count}/{len(entries)} with media", inline=True)

    # Add eBird link for representative
    ebird_url = f"https://ebird.org/checklist/{representative.checklist_id}"
    embed.add_field(name="Representative Checklist", value=f"[View eBird]({ebird_url})", inline=True)

    # Add footer
    embed.set_footer(text="This is a cluster of nearby reports. Accept All to add to RBA or Reject All to dismiss.")

    return embed


async def send_moderation_requests(channel: discord.TextChannel, entries: List[ModerationEntry]) -> int:
    """
    Send moderation requests for entries, using clustering when appropriate.
    Returns the number of successfully sent requests.
    """
    if not entries:
        return 0
    
    sent_count = 0
    
    # Determine if we should use clustering
    if should_cluster_for_moderation(entries):
        logger.info(f"Clustering {len(entries)} moderation entries")
        clusters = cluster_moderation_entries(entries, threshold_km=2)
        
        for cluster_id, cluster_entries in clusters.items():
            if len(cluster_entries) == 1:
                # Single entry - use individual moderation
                entry = cluster_entries[0]
                message = await send_individual_moderation_request(channel, entry)
                if message:
                    sent_count += 1
            else:
                # Multiple entries - use cluster moderation
                message = await send_cluster_moderation_request(channel, cluster_id, cluster_entries)
                if message:
                    sent_count += 1
    else:
        # No clustering needed - send individual requests
        for entry in entries:
            message = await send_individual_moderation_request(channel, entry)
            if message:
                sent_count += 1
    
    return sent_count


async def send_individual_moderation_request(channel: discord.TextChannel, entry: ModerationEntry) -> Optional[discord.Message]:
    """Send a moderation request for a single entry."""
    try:
        embed = create_moderation_embed(entry)
        view = ModerationView(entry.checklist_id, entry.species)
        
        message = await channel.send(embed=embed, view=view)
        
        # Update the moderation entry with the Discord message ID
        update_discord_message_id(entry.checklist_id, entry.species, str(message.id))
        
        return message
        
    except Exception as e:
        logger.error(f"Failed to send individual moderation request: {e}")
        return None


async def send_cluster_moderation_request(channel: discord.TextChannel, cluster_id: str, entries: List[ModerationEntry]) -> Optional[discord.Message]:
    """Send a moderation request for a cluster of entries."""
    try:
        embed = create_cluster_moderation_embed(cluster_id, entries)
        view = ClusterModerationView(cluster_id, entries)
        
        message = await channel.send(embed=embed, view=view)
        
        # Update all entries in the cluster with the Discord message ID
        for entry in entries:
            update_discord_message_id(entry.checklist_id, entry.species, str(message.id))
        
        return message
        
    except Exception as e:
        logger.error(f"Failed to send cluster moderation request: {e}")
        return None