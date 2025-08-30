# clustering_utils.py
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import radians, sin, cos, sqrt, atan2
from typing import List, Dict, Tuple
from models import Observation, ModerationEntry
import logging

logger = logging.getLogger("Dipper_RBA_Bot")

def haversine(lat1, lon1, lat2, lon2):
    """Calculate the great circle distance between two points on Earth in kilometers."""
    R = 6371.0  # Earth's radius in kilometers
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

def normalize_species_name(name: str) -> str:
    """Normalize species name for clustering by removing subspecies info."""
    base = name.split("(")[0].strip()
    return "".join(c.lower() for c in base if c.isalnum() or c.isspace())

def cluster_observations(observations: List[Observation], threshold_km=2) -> Dict[Tuple, List[Observation]]:
    """
    Cluster observations by species and proximity.
    
    Args:
        observations: List of Observation objects
        threshold_km: Distance threshold in kilometers for clustering
        
    Returns:
        Dictionary mapping (species, lat, lon, location) -> List[Observation]
    """
    clusters = {}  # key -> list[obs]

    for obs in observations:
        if obs.lat is None or obs.lon is None:
            # Skip observations without coordinates - treat as individual
            key = (obs.species, None, None, obs.location or "Unknown")
            if key not in clusters:
                clusters[key] = []
            clusters[key].append(obs)
            continue
            
        norm_name = normalize_species_name(obs.species)
        lat, lon = float(obs.lat), float(obs.lon)

        match_key = None
        for (sp, clat, clon, loc) in clusters.keys():
            # Skip clusters without coordinates
            if clat is None or clon is None:
                continue
                
            if normalize_species_name(sp) != norm_name:
                continue
            if haversine(lat, lon, clat, clon) <= threshold_km:
                match_key = (sp, clat, clon, loc)
                break

        if match_key:
            clusters[match_key].append(obs)
        else:
            key = (obs.species, lat, lon, obs.location or "Unknown")
            clusters[key] = [obs]

    return clusters

def get_cluster_representative(observations: List[Observation]) -> Observation:
    """
    Get the representative observation for a cluster (most recent with coordinates).
    
    Args:
        observations: List of observations in the cluster
        
    Returns:
        The observation that best represents the cluster
    """
    # Sort by datetime, newest first
    sorted_obs = sorted(observations, key=lambda o: o.obs_datetime, reverse=True)
    
    # Prefer observations with coordinates and media
    for obs in sorted_obs:
        if obs.lat is not None and obs.lon is not None and obs.has_media:
            return obs
    
    # Then prefer observations with coordinates
    for obs in sorted_obs:
        if obs.lat is not None and obs.lon is not None:
            return obs
    
    # Finally, just return the most recent
    return sorted_obs[0]

def create_cluster_id(observations: List[Observation]) -> str:
    """
    Create a unique cluster ID based on the observations.
    
    Args:
        observations: List of observations in the cluster
        
    Returns:
        Unique cluster identifier string
    """
    if not observations:
        return "empty_cluster"
    
    representative = get_cluster_representative(observations)
    checklist_ids = sorted([obs.checklist_id for obs in observations])
    
    # Format: species_lat_lon_count_firstchecklist
    species_normalized = normalize_species_name(representative.species)
    lat_str = f"{representative.lat:.3f}" if representative.lat else "none"
    lon_str = f"{representative.lon:.3f}" if representative.lon else "none"
    
    return f"{species_normalized}_{lat_str}_{lon_str}_{len(observations)}_{checklist_ids[0]}"

def cluster_moderation_entries(entries: List[ModerationEntry], threshold_km=2) -> Dict[str, List[ModerationEntry]]:
    """
    Cluster moderation entries by species and proximity for group moderation.
    
    Args:
        entries: List of ModerationEntry objects
        threshold_km: Distance threshold in kilometers for clustering
        
    Returns:
        Dictionary mapping cluster_id -> List[ModerationEntry]
    """
    # Convert ModerationEntry to Observation for clustering
    observations = []
    entry_map = {}  # observation -> entry mapping
    
    for entry in entries:
        obs = Observation(
            checklist_id=entry.checklist_id,
            species=entry.species,
            subspecies=None,
            region=entry.region,
            location=entry.location,
            observer=entry.observer,
            obs_datetime=entry.obs_datetime,
            local_tz=entry.local_tz,
            thread_tracker_key=None,
            lat=entry.lat,
            lon=entry.lon,
            counted=False,
            has_media=entry.has_media
        )
        observations.append(obs)
        entry_map[obs] = entry
    
    # Cluster observations
    obs_clusters = cluster_observations(observations, threshold_km)
    
    # Convert back to moderation entry clusters
    entry_clusters = {}
    for cluster_key, obs_list in obs_clusters.items():
        cluster_entries = [entry_map[obs] for obs in obs_list]
        cluster_id = create_cluster_id(obs_list)
        entry_clusters[cluster_id] = cluster_entries
        
        logger.info(f"Created moderation cluster {cluster_id} with {len(cluster_entries)} entries for {cluster_key[0]}")
    
    return entry_clusters

def should_cluster_for_moderation(entries: List[ModerationEntry]) -> bool:
    """
    Determine if moderation entries should be clustered.
    
    Returns True if there are multiple entries that could benefit from clustering.
    """
    if len(entries) < 2:
        return False
    
    # Group by species
    species_groups = defaultdict(list)
    for entry in entries:
        species_groups[normalize_species_name(entry.species)].append(entry)
    
    # Check if any species has multiple entries with coordinates
    for species, species_entries in species_groups.items():
        if len(species_entries) > 1:
            entries_with_coords = [e for e in species_entries if e.lat is not None and e.lon is not None]
            if len(entries_with_coords) > 1:
                return True
    
    return False