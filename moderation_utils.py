# moderation_utils.py
import json
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple
from models import ModerationEntry, ModerationStatus, Observation
from mappers import split_species_and_subspecies
from clustering_utils import cluster_observations, get_cluster_representative
import logging

logger = logging.getLogger("Dipper_RBA_Bot")

# These functions are now imported from db.py
from db import (
    get_moderation_entry_by_composite as get_moderation_entry,
    get_pending_moderation_entries,
    update_moderation_entry_status_by_composite as update_moderation_entry_status,
    update_discord_message_id_by_composite as update_discord_message_id,
    is_already_processed_by_composite as is_already_processed,
    save_moderation_entry as db_save_moderation_entry
)


def load_review_species_from_file(filepath: str = "CO_Review_List.txt") -> dict[str, bool]:
    """Load the species review list from JSON file."""
    try:
        with open(filepath, 'r') as f:
            review_dict = json.load(f)
        # Convert "True"/"False" strings to actual booleans
        return {species: flag == "True" for species, flag in review_dict.items()}
    except FileNotFoundError:
        logger.error(f"Review species file not found: {filepath}")
        return {}
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in review species file: {filepath}")
        return {}


def normalize_species_for_review(full_species_name: str) -> str:
    """
    Normalize species name by removing subspecies information.
    Example: "Canada Goose (Atlantic)" -> "Canada Goose"
    """
    species, _ = split_species_and_subspecies(full_species_name)
    return species


def should_species_be_moderated(species_name: str, review_species: dict[str, bool]) -> bool:
    """
    Check if a species should be sent to moderation based on the review list.
    """
    normalized_species = normalize_species_for_review(species_name)
    return review_species.get(normalized_species, False)


def create_moderation_entry(obs: Observation) -> ModerationEntry:
    """
    Create a new moderation entry from an observation.
    """
    return ModerationEntry(
        id=0,  # Will be set by database
        checklist_id=obs.checklist_id,
        species=obs.species,
        region=obs.region,
        observer=obs.observer,
        location=obs.location,
        obs_datetime=obs.obs_datetime,
        local_tz=obs.local_tz,
        lat=obs.lat,
        lon=obs.lon,
        has_media=obs.has_media,
        submitted_at=datetime.now(),
        status=ModerationStatus.PENDING
    )


def save_moderation_entry(entry: ModerationEntry) -> int:
    """
    Save a moderation entry to database. Returns the entry ID.
    This is now a wrapper around the db.py function.
    """
    return db_save_moderation_entry(entry)


def filter_new_rare_species(observations: List[Observation], review_species: dict[str, bool]) -> List[Observation]:
    """
    Filter observations to only include:
    1. Species that should be moderated (in review list with True)
    2. Species that haven't already been processed for this checklist
    """
    new_rare_obs = []
    
    for obs in observations:
        # Check if species should be moderated
        if should_species_be_moderated(obs.species, review_species):
            # Check if already processed
            if not is_already_processed(obs.checklist_id, obs.species):
                new_rare_obs.append(obs)
            else:
                logger.debug(f"Skipping already processed: {obs.checklist_id} - {obs.species}")
    
    return new_rare_obs


def process_observations_for_moderation(observations: List[Observation]) -> List[ModerationEntry]:
    """
    Process a list of observations and create moderation entries for qualifying species.
    Uses clustering to group nearby observations of the same species.
    """
    # Load review species list
    review_species = load_review_species_from_file()
    if not review_species:
        logger.warning("No review species loaded, no moderation entries will be created")
        return []
    
    # Filter to new rare species
    new_rare_obs = filter_new_rare_species(observations, review_species)
    
    if not new_rare_obs:
        logger.info("No new rare species found for moderation")
        return []
    
    logger.info(f"Found {len(new_rare_obs)} new rare species observations for moderation")
    
    # Cluster observations by species and proximity (2km threshold)
    clusters = cluster_observations(new_rare_obs, threshold_km=2)
    
    moderation_entries = []
    for cluster_key, cluster_obs in clusters.items():
        species, lat, lon, location = cluster_key
        
        if len(cluster_obs) == 1:
            # Single observation - create one moderation entry
            obs = cluster_obs[0]
            entry = create_moderation_entry(obs)
            entry_id = save_moderation_entry(entry)
            entry.id = entry_id
            moderation_entries.append(entry)
            logger.info(f"Created moderation entry for {obs.species} in checklist {obs.checklist_id}")
            
        else:
            # Multiple clustered observations - create entry for representative
            representative = get_cluster_representative(cluster_obs)
            entry = create_moderation_entry(representative)
            entry_id = save_moderation_entry(entry)
            entry.id = entry_id
            moderation_entries.append(entry)
            
            # Also create entries for other observations in cluster, but mark them differently
            # This ensures we track all observations but present them as a group
            for obs in cluster_obs:
                if obs.checklist_id != representative.checklist_id:
                    cluster_entry = create_moderation_entry(obs)
                    cluster_entry_id = save_moderation_entry(cluster_entry)
                    cluster_entry.id = cluster_entry_id
                    moderation_entries.append(cluster_entry)
            
            logger.info(f"Created clustered moderation entries for {species} - {len(cluster_obs)} observations at {location}")
    
    return moderation_entries


def process_clustered_observations_for_moderation(observations: List[Observation]) -> List[ModerationEntry]:
    """
    Alternative processing method that creates one moderation entry per cluster.
    This reduces the number of moderation requests but may lose some granularity.
    """
    # Load review species list
    review_species = load_review_species_from_file()
    if not review_species:
        logger.warning("No review species loaded, no moderation entries will be created")
        return []
    
    # Filter to new rare species
    new_rare_obs = filter_new_rare_species(observations, review_species)
    
    if not new_rare_obs:
        logger.info("No new rare species found for moderation")
        return []
    
    # Cluster observations
    clusters = cluster_observations(new_rare_obs, threshold_km=2)
    
    moderation_entries = []
    for cluster_key, cluster_obs in clusters.items():
        species, lat, lon, location = cluster_key
        
        # Get the representative observation for this cluster
        representative = get_cluster_representative(cluster_obs)
        
        # Create a single moderation entry for the entire cluster
        entry = create_moderation_entry(representative)
        entry_id = save_moderation_entry(entry)
        entry.id = entry_id
        moderation_entries.append(entry)
        
        if len(cluster_obs) > 1:
            logger.info(f"Created clustered moderation entry for {species} representing {len(cluster_obs)} observations at {location}")
        else:
            logger.info(f"Created moderation entry for {species} in checklist {representative.checklist_id}")
    
    return moderation_entries# moderation_utils.py
import json
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple
from models import ModerationEntry, ModerationStatus, Observation
from mappers import split_species_and_subspecies
from db import get_connection
import logging

logger = logging.getLogger("Dipper_RBA_Bot")

def load_review_species_from_file(filepath: str = "CO_Review_List.txt") -> dict[str, bool]:
    """Load the species review list from JSON file."""
    try:
        with open(filepath, 'r') as f:
            review_dict = json.load(f)
        # Convert "True"/"False" strings to actual booleans
        return {species: flag == "True" for species, flag in review_dict.items()}
    except FileNotFoundError:
        logger.error(f"Review species file not found: {filepath}")
        return {}
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in review species file: {filepath}")
        return {}


def normalize_species_for_review(full_species_name: str) -> str:
    """
    Normalize species name by removing subspecies information.
    Example: "Canada Goose (Atlantic)" -> "Canada Goose"
    """
    species, _ = split_species_and_subspecies(full_species_name)
    return species


def should_species_be_moderated(species_name: str, review_species: dict[str, bool]) -> bool:
    """
    Check if a species should be sent to moderation based on the review list.
    """
    normalized_species = normalize_species_for_review(species_name)
    return review_species.get(normalized_species, False)


def is_already_processed(checklist_id: str, species: str) -> bool:
    """
    Check if this checklist+species combo has already been processed 
    (either accepted or rejected).
    This is now a wrapper around the db.py function.
    """
    from db import is_already_processed_by_composite
    return is_already_processed_by_composite(checklist_id, species)


def create_moderation_entry(obs: Observation) -> ModerationEntry:
    """
    Create a new moderation entry from an observation.
    """
    return ModerationEntry(
        id=0,  # Will be set by database
        checklist_id=obs.checklist_id,
        species=obs.species,
        region=obs.region,
        observer=obs.observer,
        location=obs.location,
        obs_datetime=obs.obs_datetime,
        local_tz=obs.local_tz,
        lat=obs.lat,
        lon=obs.lon,
        has_media=obs.has_media,
        submitted_at=datetime.now(),
        status=ModerationStatus.PENDING
    )


def save_moderation_entry(entry: ModerationEntry) -> int:
    """
    Save a moderation entry to database. Returns the entry ID.
    This is now a wrapper around the db.py function.
    """
    from db import save_moderation_entry as db_save_moderation_entry
    return db_save_moderation_entry(entry)


# These functions are now imported from db.py
from db import (
    get_moderation_entry_by_composite as get_moderation_entry,
    get_pending_moderation_entries,
    update_moderation_entry_status_by_composite as update_moderation_entry_status,
    update_discord_message_id_by_composite as update_discord_message_id,
    is_already_processed_by_composite as is_already_processed,
    save_moderation_entry
)


def filter_new_rare_species(observations: List[Observation], review_species: dict[str, bool]) -> List[Observation]:
    """
    Filter observations to only include:
    1. Species that should be moderated (in review list with True)
    2. Species that haven't already been processed for this checklist
    """
    new_rare_obs = []
    
    for obs in observations:
        # Check if species should be moderated
        if should_species_be_moderated(obs.species, review_species):
            # Check if already processed
            if not is_already_processed(obs.checklist_id, obs.species):
                new_rare_obs.append(obs)
            else:
                logger.debug(f"Skipping already processed: {obs.checklist_id} - {obs.species}")
    
    return new_rare_obs


def process_observations_for_moderation(observations: List[Observation]) -> List[ModerationEntry]:
    """
    Process a list of observations and create moderation entries for qualifying species.
    """
    # Load review species list
    review_species = load_review_species_from_file()
    if not review_species:
        logger.warning("No review species loaded, no moderation entries will be created")
        return []
    
    # Filter to new rare species
    new_rare_obs = filter_new_rare_species(observations, review_species)
    
    if not new_rare_obs:
        logger.info("No new rare species found for moderation")
        return []
    
    # Create moderation entries
    moderation_entries = []
    for obs in new_rare_obs:
        entry = create_moderation_entry(obs)
        entry_id = save_moderation_entry(entry)
        entry.id = entry_id
        moderation_entries.append(entry)
        logger.info(f"Created moderation entry for {obs.species} in checklist {obs.checklist_id}")
    
    return moderation_entries