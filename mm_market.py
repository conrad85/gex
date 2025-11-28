# mm_market.py
import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")

def fetch_market_data():
    """Pobiera market z publicznego endpointu /api/market (lista par)."""

    url = f"{API_BASE}/api/market"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        return {"error": str(e), "pairs": []}

    data = r.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # gdyby backend zwracał obiekt zamiast listy
        return data.get("pairs", [])
    return []
