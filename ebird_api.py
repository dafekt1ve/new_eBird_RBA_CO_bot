# ebird_api.py
import time
import requests
from datetime import datetime
from models import Observation
import os
from dotenv import load_dotenv

load_dotenv()
EBIRD_TOKEN = os.getenv("EBIRD_TOKEN")
if not EBIRD_TOKEN:
    raise RuntimeError("EBIRD_TOKEN not set in .env")

def fetch_ebird_rba(region_code, retries=3, delay=5):
    url = f"https://api.ebird.org/v2/data/obs/{region_code}/recent/notable?detail=full&back=2&maxResults=200"
    headers = {"X-eBirdApiToken": EBIRD_TOKEN}
    
    for attempt in range(retries):
        try:
            res = requests.get(url, headers=headers)
            res.raise_for_status()
            return res.json()
        except requests.exceptions.HTTPError as e:
            if 500 <= res.status_code < 600:
                # Server error, wait and retry
                print(f"Server error {res.status_code}, retrying in {delay}s...")
                time.sleep(delay)
            else:
                # Client error, re-raise
                raise
    raise RuntimeError(f"Failed to fetch data for {region_code} after {retries} attempts")
