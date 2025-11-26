import os
from datetime import datetime, timezone

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

DB_PARAMS = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "gex"),
    "user": os.getenv("DB_USER", "gex_user"),
    "password": os.getenv("DB_PASS", "gex_pass"),
}

MEXC_TICKER_URL = "https://api.mexc.com/api/v3/ticker/price?symbol=VEEUSDT"
# Dexscreener token price (Arbitrum VEE)
DEXSCREENER_URL = (
    "https://api.dexscreener.com/latest/dex/tokens/"
    "0x0caadd427a6feb5b5fc1137eb05aa7ddd9c08ce9"
)


def get_vee_price_from_mexc():
    resp = requests.get(MEXC_TICKER_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return float(data["price"])


def get_vee_price_from_dexscreener():
    resp = requests.get(DEXSCREENER_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    # struktura: {"pairs":[{"priceUsd":"0.0123", ...}, ...]}
    pairs = data.get("pairs") or []
    if not pairs:
        raise RuntimeError("No pairs in dexscreener response")
    price_str = pairs[0].get("priceUsd")
    if price_str is None:
        raise RuntimeError("Missing priceUsd in dexscreener response")
    return float(price_str)


def save_price_to_db(price: float, source: str):
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO vee_price_snapshots (price_usd, source)
        VALUES (%s, %s)
        """,
        (price, source),
    )
    conn.commit()
    cur.close()
    conn.close()


def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Fetching VEE price...")

    price = None
    source = None

    # prefer MEXC, fallback Dexscreener
    try:
        price = get_vee_price_from_mexc()
        source = "mexc_vee_usdt"
        print(f"[{now.isoformat()}] VEE_USD = {price:.8f} (MEXC)")
    except Exception as e:
        print(f"[{now.isoformat()}] WARN: MEXC fetch failed ({e!r}), trying dexscreener")
        try:
            price = get_vee_price_from_dexscreener()
            source = "dexscreener_arbitrum"
            print(f"[{now.isoformat()}] VEE_USD = {price:.8f} (Dexscreener)")
        except Exception as e2:
            print(f"[{now.isoformat()}] ERROR: all sources failed ({e2!r})")
            return

    try:
        save_price_to_db(price, source)
        print(f"[{now.isoformat()}] Saved to vee_price_snapshots.")
    except Exception as e:
        print(f"[{now.isoformat()}] ERROR while saving VEE price: {e!r}")


if __name__ == "__main__":
    main()
