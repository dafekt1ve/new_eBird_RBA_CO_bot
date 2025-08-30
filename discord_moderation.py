# discord_moderation.py
import discord
from datetime import datetime
from typing import List, Optional
from models import ModerationEntry, ModerationStatus
from moderation_utils import (
    update_moderation_entry_status, 
    update_discord_message_id,
    get_moderation_entry
)
from db import save_checklist, save_thread
from models import Observation, ThreadRecord, ThreadType
from thread_rba_content import create_thread_message
import logging

logger = logging.getLogger("Dipper_RBA_Bot")

class ModerationView(discord.ui.View):
    """Discord View for moderation Accept/Reject buttons."""
    
    def __init__(self, checklist_id: str, species: str):
        super().__init__(timeout=None)  # No timeout since we don't want buttons to expire
        self.checklist_id = checklist_id
        self.species = species

    @discord.ui.button(label='Accept', style=discord.ButtonStyle.green, emoji='✅')
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_moderation_action(interaction, ModerationStatus.ACCEPTED)

    @discord.ui.button(label='Reject', style=discord.ButtonStyle.red, emoji='❌')
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_moderation_action(interaction, ModerationStatus.REJECTED)

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


def create_moderation_embed(entry: ModerationEntry) -> discord.Embed:
    """Create a Discord embed for a moderation entry."""
    
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


async def send_moderation_request(channel: discord.TextChannel, entry: ModerationEntry) -> Optional[discord.Message]:
    """
    Send a moderation request message to the specified channel.
    Returns the sent message or None if failed.
    """
    try:
        embed = create_moderation_embed(entry)
        view = ModerationView(entry.checklist_id, entry.species)
        
        message = await channel.send(embed=embed, view=view)
        
        # Update the moderation entry with the Discord message ID
        update_discord_message_id(entry.checklist_id, entry.species, str(message.id))
        
        return message
        
    except Exception as e:
        logger.error(f"Failed to send moderation request: {e}")
        return None


async def send_moderation_requests(channel: discord.TextChannel, entries: List[ModerationEntry]) -> int:
    """
    Send moderation requests for multiple entries.
    Returns the number of successfully sent requests.
    """
    sent_count = 0
    
    for entry in entries:
        message = await send_moderation_request(channel, entry)
        if message:
            sent_count += 1
            logger.info(f"Sent moderation request for {entry.species} in {entry.checklist_id}")
        else:
            logger.error(f"Failed to send moderation request for {entry.species} in {entry.checklist_id}")
    
    return sent_count