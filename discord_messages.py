import discord
from datetime import datetime
from moderation_utils import ClusterModerationView, make_cluster_id
from co_county_lookup import get_county_name_from_code
from cluster_registry import cluster_mapping
from typing import List
import logging

# Just get the logger by name — NO handlers here
logger = logging.getLogger("Dipper_RBA_Bot")

def build_cluster_moderation_message(obs_cluster: List[dict], merge_options=None):
    if not obs_cluster:
        raise ValueError("obs_cluster must not be empty")

    species = obs_cluster[0]["comName"]
    # region = obs_cluster[0].get("region", "Unknown")
    subnational2 = obs_cluster[0].get("subnational2Code", "")
    county = get_county_name_from_code(subnational2)
    location_name = obs_cluster[0].get("locName", "Unknown")
    lat = obs_cluster[0]["lat"]
    lon = obs_cluster[0]["lng"]

    sorted_obs = sorted(obs_cluster, key=lambda o: o.get("obsDt"), reverse=True)
    first_obs = sorted_obs[0]
    first_name = first_obs.get("userDisplayName", "Unknown")
    first_checklist = first_obs.get("subId")
    first_dt = first_obs.get("obsDt")[:16].replace("T", " ")
    try:
        obs_dt = datetime.strptime(first_dt, "%Y-%m-%d %H:%M")
    except ValueError:
        obs_dt = datetime.strptime(first_dt, "%Y-%m-%d")


    embed = discord.Embed(title=f"{species}, {county}, {obs_dt:%b %Y}", color=discord.Color.orange())
    embed.add_field(
        name="Location",
        value=f"[{location_name}](https://www.google.com/maps/search/?api=1&query={lat},{lon})",
        inline=False,
    )
    embed.add_field(
        name="First Observer",
        value=f"[{first_name}](https://ebird.org/checklist/{first_checklist}) ({first_dt})",
        inline=False,
    )

    # Other observers
    others = sorted_obs[1:]
    unique_others = []
    seen = set()
    for o in others:
        name = o.get("userDisplayName", "Unknown")
        if name not in seen:
            unique_others.append(o)
            seen.add(name)
    displayed_others = unique_others[:10]
    if displayed_others:
        observers_str = ", ".join(
            f"[{o.get('userDisplayName','Unknown')}](https://ebird.org/checklist/{o.get('subId')})"
            for o in displayed_others
        )
        more_count = max(0, len(unique_others) - len(displayed_others))
        if more_count:
            observers_str += f", and {more_count} more"
        embed.add_field(name="Also reported by", value=observers_str, inline=False)

    # Cluster ID and view
    checklist_ids = [o.get("subId") for o in obs_cluster]
    cluster_id = make_cluster_id(checklist_ids)
    cluster_mapping[cluster_id] = set(checklist_ids)

    logger.info(f"Creating moderation embed for {species} in cluster {cluster_id}, first checklist {first_checklist}")
    logger.info(f"Cluster_mapping has keys: {list(cluster_mapping.keys())[:5]}...")

    view = ClusterModerationView(cluster_id, first_checklist, merge_options)

    return embed, view

