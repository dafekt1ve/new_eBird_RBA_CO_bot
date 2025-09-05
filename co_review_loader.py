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
        for species_name, is_review in review_data.items():
            species_name = species_name.strip()
            is_review_bool = str(is_review).lower() == 'true'
            
            if not species_name:
                continue
            
            # For review species (True), we don't need a threshold count
            # For non-review species (False), we'll set a default threshold or leave null
            threshold_count = None if is_review_bool else None
            
            with conn:
                conn.execute("""
                    INSERT INTO statewide_thresholds 
                    (species_code, common_name, threshold_count, is_review_species, notes, updated_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                """, (None, species_name, threshold_count, is_review_bool, 
                      "Loaded from CO_Review_List.txt"))
            
            count += 1
            status = "REVIEW" if is_review_bool else "THRESHOLD"
            logger.debug(f"Loaded: {species_name} ({status})")
        
        logger.info(f"Successfully loaded {count} species from CO Review List")
        
        # Log summary
        review_count = sum(1 for is_review in review_data.values() if str(is_review).lower() == 'true')
        threshold_count = count - review_count
        logger.info(f"  - Review species: {review_count}")
        logger.info(f"  - Threshold species: {threshold_count}")
        
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
    try:
        conn = get_connection()
        row = conn.execute("""
            SELECT * FROM statewide_thresholds 
            WHERE common_name = ? COLLATE NOCASE
        """, (species_name,)).fetchone()
        
        if row:
            return {
                'common_name': row['common_name'],
                'is_review_species': bool(row['is_review_species']),
                'threshold_count': row['threshold_count'],
                'in_review_list': True
            }
        
        # Not in review list - this means it needs moderation
        return {
            'common_name': species_name,
            'is_review_species': True,  # Default to requiring review if not in list
            'threshold_count': None,
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
    status = get_species_review_status(species_name)
    
    if not status:
        # Error checking - default to requiring moderation for safety
        return True
    
    if not status['in_review_list']:
        # Not in CO Review List - requires moderation
        logger.debug(f"{species_name} not in CO Review List - requires moderation")
        return True
    
    if status['is_review_species']:
        # Marked as review species - requires moderation
        logger.debug(f"{species_name} is review species - requires moderation")
        return True
    
    # In list and marked as "False" - could implement threshold logic here
    # For now, we'll say these don't need moderation unless you want threshold logic
    logger.debug(f"{species_name} is in review list as threshold species - no moderation needed")
    return False

def get_review_list_summary() -> dict:
    """Get summary of loaded review list"""
    try:
        conn = get_connection()
        
        total = conn.execute("SELECT COUNT(*) FROM statewide_thresholds").fetchone()[0]
        review_species = conn.execute(
            "SELECT COUNT(*) FROM statewide_thresholds WHERE is_review_species = 1"
        ).fetchone()[0]
        threshold_species = total - review_species
        
        return {
            'total_species': total,
            'review_species': review_species,
            'threshold_species': threshold_species,
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