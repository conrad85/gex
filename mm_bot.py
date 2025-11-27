#!/usr/bin/env python3
import os
import json
import argparse
import time
from datetime import datetime
import logging
import requests
from dotenv import load_dotenv

from mm_market import fetch_market_data

load_dotenv()

# LOGGING =====================================================================
LOG_FILE = "mm_bot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# CONFIG ======================================================================
CONFIG = {
    "wallet": os.getenv("MM_WALLET"),
    "exit_pct": float(os.getenv("MM_EXIT_PCT", "2")),
    "enter_size": float(os.getenv("MM_ENTER_SIZE", "10000")),
}

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")

# API HELPERS =================================================================
def api(path):
    r = requests.get(f"{API_BASE}{path}", timeout=10)
    r.raise_for_status()
    return r.json()

# DISCOVERY ====================================================================
def discover_full_market():
    """Pełny skan rynku dla WSZYSTKICH LP (APR, il, reserves, vol24)."""
    pairs = fetch_market_data()
    results = []

    for p in pairs:
        results.append({
            "pair_address": p["pair_address"],
            "item_name": p["item_name"],
            "lp_apr": p.get("lp_apr"),
            "il_pct": p.get("il_pct"),
            "net_effective_pct": p.get("net_effective_pct"),
            "vol24": p.get("vol24"),
            "reserve_vee": p.get("reserve_vee"),
        })

    return results

# MAIN LOOP ===================================================================
def run_tick(loop_mode=False, interval=300):
    wallet = CONFIG["wallet"]
    if not wallet:
        logging.error("Wallet not set in .env (MM_WALLET missing).")
        return

    while True:
        try:
            logging.info(f"[MM] Fetching wallet LP: {wallet}")

            wallet_data = api(f"/api/lp/{wallet}")

            vee_usd = wallet_data.get("vee_usd_price")
            pairs = wallet_data.get("pairs", [])

            logging.info(f"[MM] vee_usd_price: {vee_usd}")
            logging.info(f"[MM] lp pairs tracked: {len(pairs)}")

            # -------------------- DISCOVERY SCAN --------------------
            discover = discover_full_market()
            logging.info(f"[DISCOVER] scanning {len(discover)} total LP pairs...")

            for d in discover:
                logging.info(
                    f"[D] {d['item_name']} | net {d['net_effective_pct']}% | "
                    f"APR {d['lp_apr']} | vol24 {d['vol24']} | reserve {d['reserve_vee']}"
                )

            # -------------------- SUGGESTIONS ------------------------
            suggestions = []

            exit_level = CONFIG["exit_pct"]
            enter_size = CONFIG["enter_size"]

            for lp in pairs:
                net = lp.get("net_effective_pct", 0)

                # EXIT
                if net < exit_level:
                    suggestions.append(
                        f"[EXIT] {lp['item_name']} | current LP: {lp['value_lp_vee']:.2f} | net: {net:.2f}%"
                    )
                    continue

                # ENTER / SCALE-UP (only if good APR + liquidity)
                if net > exit_level:
                    suggestions.append(
                        f"[ENTER/UP] {lp['item_name']} | current LP: {lp['value_lp_vee']:.2f} "
                        f"-> +{enter_size:.2f} VEE | net: {net:.2f}%"
                    )

            logging.info(f"[MM] Suggested actions ({len(suggestions)}):")
            for s in suggestions:
                logging.info(s)

            logging.info("[MM] End of tick.")
        except Exception as e:
            logging.error(f"Tick error: {e}")

        if not loop_mode:
            break

        time.sleep(interval)

# CLI ========================================================================
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--loop", action="store_true")
    p.add_argument("--interval", type=int, default=300)
    args = p.parse_args()

    run_tick(loop_mode=args.loop, interval=args.interval)
