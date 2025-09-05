# moderation_utils.py
from collections import defaultdict
import hashlib
import discord
from discord.ui import View, Button, Select
from typing import List, Dict, Tuple
from db import update_moderation_status
from cluster_registry import cluster_mapping
from models import ModerationStatus
from dotenv import load_dotenv 
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from thread_rba_content import build_rba_content_from_cluster
import sqlite3
import logging

# Just get the logger by name — NO handlers here
logger = logging.getLogger("Dipper_RBA_Bot")

load_dotenv()
STATEWIDE_RBA_CHANNEL_ID = os.getenv("STATEWIDE_FORUM_CHANNEL_ID")
DB_PATH = f"./data/{os.getenv('DB_FILE')}"

# ----------------------
# Clustering Utilities
# ----------------------

def make_cluster_id(checklist_ids: list[str]) -> str:
    """
    Generate a short 32-char hash ID for a cluster of checklist IDs.
    Safe for Discord button custom_id.
    """
    joined = ','.join(checklist_ids)
    return hashlib.md5(joined.encode()).hexdigest()

# ----------------------
# MergeDropdown (reusable across contexts)
# ----------------------
class MergeDropdown(Select):
    def __init__(self, merge_options, cluster_id, checklist_id):
        # Convert string options to dicts if necessary
        formatted_options = [
            {"label": o, "value": o} if isinstance(o, str) else o for o in merge_options
        ]
        super().__init__(
            placeholder="Merge with existing cluster…",
            options=[
                discord.SelectOption(label=o["label"], value=o["value"])
                for o in formatted_options
            ],
        )
        self.cluster_id = cluster_id
        self.checklist_id = checklist_id

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]
        # Here you can implement the merge logic
        await interaction.response.send_message(
            f"Checklist {self.checklist_id} merged with {selected_value}", ephemeral=True
        )

class ClusterModerationView(View):
    def __init__(self, cluster_id, first_checklist_id, merge_options=None):
        super().__init__(timeout=None)
        self.cluster_id = cluster_id
        self.first_checklist_id = first_checklist_id

        # Approve button
        approve_btn = Button(label="✅ Accept", style=discord.ButtonStyle.success)
        approve_btn.callback = self.accept_callback
        self.add_item(approve_btn)

        # Reject button
        reject_btn = Button(label="❌ Reject", style=discord.ButtonStyle.danger)
        reject_btn.callback = self.reject_callback
        self.add_item(reject_btn)

        # Optional merge dropdown
        if merge_options:
            self.add_item(MergeDropdown(merge_options, cluster_id, first_checklist_id))

    async def disable_all(self, interaction: discord.Interaction, status: str, color: discord.Color):
        # disable all buttons in the view
        for child in self.children:
            child.disabled = True

        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            embed.color = color
            embed.add_field(name="Decision", value=status, inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message(status, ephemeral=True)

async def accept_callback(self, interaction: discord.Interaction):
        try:
            logger.info(f"accept_callback triggered for cluster {getattr(self, 'cluster_id', 'MISSING')}")
        except Exception as e:
            logger.exception(f"accept_callback failed before first log: {e}")
            await interaction.response.send_message("Error processing accept button", ephemeral=True)
            return

        # 1️⃣ Update moderation status for all checklists in this cluster
        now_utc = datetime.now(tz=ZoneInfo("UTC"))

        # Batch update moderation status in memory
        for checklist_id in cluster_mapping[self.cluster_id]:
            update_moderation_status(
                checklist_id,
                "accepted",
                moderated_by=str(interaction.user),
                moderated_at=now_utc
            )
            logger.info(f"Accepted cluster {self.cluster_id}: {checklist_id}")

        # Batch update thread_tracker_key in DB
        conn = sqlite3.connect(DB_PATH)
        with conn:
            placeholders = ",".join("?" for _ in cluster_mapping[self.cluster_id])
            conn.execute(
                f"""
                UPDATE checklists
                SET thread_tracker_key = ?,
                    moderation_status = ?,
                    moderated_by = ?,
                    moderated_at = ?
                WHERE checklist_id IN ({placeholders})
                """,
                (self.cluster_id, "accepted", str(interaction.user), now_utc.isoformat(), *cluster_mapping[self.cluster_id])
            )

            # 🔹 Debug: verify DB updates
            cur = conn.cursor()
            cur.execute(
                f"SELECT checklist_id, thread_tracker_key FROM checklists WHERE checklist_id IN ({placeholders})",
                tuple(cluster_mapping[self.cluster_id])
            )
            logger.info(f"DB has these checklist_ids before update: {cur.fetchall()}")
            
            for row in cur.fetchall():
                logger.info(f"DB check: {row}")

        conn.close()

        # 2️⃣ Disable buttons and update moderation embed
        await self.disable_all(interaction, "✅ Accepted", discord.Color.green())

        # Grab the embed to extract the title info (Species, County, Month Year)
        embed = interaction.message.embeds[0]
        title = embed.title  # e.g., "Wood Stork, Broomfield, Aug 2025"

        # 3️⃣ Create forum post if it doesn't exist
        forum_channel = interaction.guild.get_channel(STATEWIDE_RBA_CHANNEL_ID)
        if forum_channel:
            # Look for an existing thread for this cluster
            existing_thread = None
            for thread in forum_channel.threads:
                if thread.name.startswith(title):
                    existing_thread = thread
                    break

            if not existing_thread:
                # Build RBA-style content from DB using accepted checklists
                content = build_rba_content_from_cluster(self.cluster_id)

                # Create a new forum thread
                thread = await forum_channel.create_thread(
                    name=title,
                    content=content
                )

        async def reject_callback(self, interaction: discord.Interaction):
            # 1️⃣ Update moderation status
            for checklist_id in cluster_mapping[self.cluster_id]:
                update_moderation_status(checklist_id, "rejected", moderated_by=str(interaction.user))
                logger.info(f"Rejected cluster {self.cluster_id}: {checklist_id}")

            # 2️⃣ Disable buttons and update embed
            await self.disable_all(interaction, "❌ Rejected", discord.Color.red())