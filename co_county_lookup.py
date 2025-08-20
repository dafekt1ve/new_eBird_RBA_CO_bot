#CO_county_lookup.py
import sqlite3
import requests
import os
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()
EBIRD_TOKEN = os.getenv("EBIRD_TOKEN")
DB_PATH = "./data/dipper_bot.db"

def fetch_us_subnational_regions():
    url = "https://api.ebird.org/v2/ref/region/list/subnational2/US-CO"
    headers = {"X-eBirdApiToken": EBIRD_TOKEN}
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    return res.json()  # list of dicts

def create_regions_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS regions (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT
        )
    """)
    conn.commit()

def upsert_region(conn, code, name, rtype):
    conn.execute("""
        INSERT INTO regions (code, name, type)
        VALUES (?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET name=excluded.name, type=excluded.type
    """, (code, name, rtype))
    conn.commit()

def ingest_regions_to_db():
    regions = fetch_us_subnational_regions()
    conn = sqlite3.connect(DB_PATH)
    create_regions_table(conn)
    
    for r in regions:
        upsert_region(conn, r["code"], r["name"], r.get("type"))
    
    conn.close()
    print(f"Ingested {len(regions)} regions.")

def lookup_region_code(name: str) -> str | None:
    name = name.strip().lower()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT code, name FROM regions")
    rows = cur.fetchall()
    conn.close()
    
    for code, db_name in rows:
        db_name_lower = db_name.lower()
        if name == db_name_lower or name in db_name_lower.split():
            return code
    return None

def get_all_county_regions() -> List[Dict[str, str]]:
    """
    Returns a list of all counties in US-CO from the regions table.
    Each item is a dict: {"code": <eBird region code>, "name": <county name>}
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT code, name 
        FROM regions 
        WHERE code LIKE 'US-CO-%'
        ORDER BY name
    """)

    rows = cur.fetchall()
    conn.close()

    return [{"code": code, "name": name} for code, name in rows]

# Example usage:
ingest_regions_to_db()
# print(lookup_region_code("Boulder"))
