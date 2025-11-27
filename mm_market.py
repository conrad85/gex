# mm_market.py
import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")

def fetch_market_data():
    """Zwraca pełne dane rynku dla WSZYSTKICH LP, bez względu na to czy user je posiada."""

    url = f"{API_BASE}/api/market/raw"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        return {"error": str(e), "pairs": []}

    data = r.json()
    pairs = data.get("pairs", [])

    # zwróć bez modyfikacji – bot to wykorzysta dalej
    return pairs
