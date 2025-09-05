# geo_utils.py - Geographic utility functions
from math import radians, sin, cos, sqrt, atan2

def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees)
    Returns distance in kilometers.
    """
    R = 6371.0  # Radius of the Earth in kilometers
    
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    
    return R * c

def normalize_species_name(name: str) -> str:
    """
    Normalize species name for clustering by removing subspecies info
    and converting to lowercase alphanumeric.
    """
    base = name.split("(")[0].strip()
    return "".join(c.lower() for c in base if c.isalnum() or c.isspace())