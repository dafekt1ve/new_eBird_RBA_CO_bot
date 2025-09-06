# co_review_loader.py - Load Colorado Review List on startup
import json
import logging
import os
from db import get_connection

logger = logging.getLogger("Dipper_RBA_Bot")

def load_co_review_list(filepath: str = "CO_Review_List.txt"):
    """
    Load CO Review List from JSON file into database.
    File format: {"Species Name": "True/False", ...}
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            review_data = json.load(file)
        
        conn = get_connection()
        
        # Clear existing thresholds
        with conn:
            conn.execute("DELETE FROM statewide_thresholds")
        
        count = 0
        review_count = 0
        for species_name, is_review in review_data.items():
            logger.info(f"Loading species: {species_name} - Review: {is_review}")
            species_name = species_name.strip()
            is_review_bool = str(is_review).lower() == 'true'
            if not species_name:
                continue
            
            count += 1

            with conn:
                conn.execute("""
                    INSERT INTO statewide_thresholds 
                    (common_name, is_review_species)
                    VALUES (?, ?)
                """, (species_name, is_review_bool))
            
            if is_review_bool:
                logger.info(f"Loaded from CO_Review_List.txt: {species_name} (REVIEW)")
                review_count += 1
        
        logger.info(f"Successfully loaded {count} species from CO Review List")
        
        # Log summary
        logger.info(f"  - Review species: {review_count}")
        
        return count
        
    except FileNotFoundError:
        logger.error(f"CO Review List file not found: {filepath}")
        logger.info("The bot will continue running, but no species will be filtered for moderation")
        return 0
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in CO Review List file: {e}")
        return 0
    except Exception as e:
        logger.error(f"Error loading CO Review List: {e}")
        return 0

def get_species_review_status(species_name: str) -> dict | None:
    """
    Get review status for a species by common name.
    Returns dict with review info or None if not found.
    """
    logger.info(f"\n\nChecking review status for species: {species_name}")
    try:
        conn = get_connection()
        row = conn.execute("""
            SELECT * FROM statewide_thresholds
            WHERE common_name = ? COLLATE NOCASE
        """, (species_name,)).fetchone()

        if row:
            logger.info(f"Found review status for {species_name}: {row['is_review_species']}")
            return {
                'common_name': row['common_name'],
                'is_review_species': row['is_review_species'],
                'in_review_list': True
            }
        
        # Not in review list - this means it needs moderation
        logger.info(f"DID NOT find review status for {species_name}")
        return {
            'common_name': species_name,
            'is_review_species': True,  # Default to requiring review if not in list
            'in_review_list': False
        }
        
    except Exception as e:
        logger.error(f"Error checking species review status: {e}")
        return None

def is_species_statewide_rba(species_name: str) -> bool:
    """
    Check if a species qualifies as statewide RBA based on CO Review List.
    
    Logic:
    - If not in list: Requires moderation (True)
    - If in list with "True": Always requires moderation (True)  
    - If in list with "False": Use threshold logic or don't require moderation (False)
    """
    if " x " in species_name:
        return False  # Hybrids don't need moderation

    status = get_species_review_status(species_name)
    logger.info(f"Species {species_name} review status: {status}")
    
    if not status:
        # Error checking - default to requiring moderation for safety
        return True
    
    if not status['in_review_list']:
        # Not in CO Review List - requires moderation
        logger.debug(f"{species_name} not in CO Review List - requires moderation")
        return True
    
    if status['is_review_species']:
        # Marked as review species - requires moderation
        logger.debug(f"{species_name} is a review species - requires moderation")
        return True
    
    # In list and marked as "False" - could implement threshold logic here
    # For now, we'll say these don't need moderation unless you want threshold logic
    # logger.debug(f"{species_name} is not a review species - no moderation needed")
    return False

def get_review_list_summary() -> dict:
    """Get summary of loaded review list"""
    try:
        conn = get_connection()
        
        review_species = conn.execute(
            "SELECT COUNT(*) FROM statewide_thresholds WHERE is_review_species = True"
        ).fetchone()[0]
        
        return {
            'review_species': review_species,
            'loaded_from': 'CO_Review_List.txt'
        }
        
    except Exception as e:
        logger.error(f"Error getting review list summary: {e}")
        return {'error': str(e)}

def reload_co_review_list(filepath: str = "CO_Review_List.txt"):
    """Reload the CO Review List (useful for updates)"""
    logger.info("Reloading CO Review List...")
    return load_co_review_list(filepath)

# Export functions for use in other modules
__all__ = [
    'load_co_review_list',
    'get_species_review_status', 
    'is_species_statewide_rba',
    'get_review_list_summary',
    'reload_co_review_list'
]