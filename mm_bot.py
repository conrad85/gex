#!/usr/bin/env python3
import os
import time
import argparse
import logging
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# === KONFIG =================================================================

MM_WALLET = os.getenv(
    "MM_WALLET",
    "0x2aEb84d9b061C850B1F3C8C5200BaE14270D49f0",
)

API_BASE = os.getenv("MM_API_BASE", "http://127.0.0.1:8000")

# progi strategii
MM_EXIT_PCT = float(os.getenv("MM_EXIT_PCT", "2.0"))           # poniżej tego – EXIT
MM_ENTER_SIZE_VEE = float(os.getenv("MM_ENTER_SIZE_VEE", "10000"))  # przy scale-up

# progi dla DISCOVER (par, w których nie masz LP)
DISCOVER_MIN_VOL24 = float(os.getenv("MM_DISCOVER_MIN_VOL24", "1000"))   # VEE
DISCOVER_MIN_RESERVE = float(os.getenv("MM_DISCOVER_MIN_RESERVE", "10000"))  # VEE

LOG_FILE = os.getenv("MM_LOG_FILE", "mm_bot.log")

# === LOGGING ================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)

# === POMOCNICZE =============================================================


def api_get(path: str):
    url = f"{API_BASE}{path}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_wallet_il(wallet: str):
    """
    /api/lp/{wallet}/il
    Zwraca dict:
    {
      "wallet": "...",
      "vee_usd_price": ...,
      "pairs": [ ... ]
    }
    """
    data = api_get(f"/api/lp/{wallet}/il")
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected IL response type: {type(data)}")

    vee_usd = float(data.get("vee_usd_price") or 0.0)
    pairs = data.get("pairs", []) or []

    return vee_usd, pairs


def fetch_market_all():
    """
    /api/market
    Zwraca listę wszystkich par z:
    - pair_address
    - item_name
    - volume_24h_vee
    - reserve_vee
    - itd.
    """
    data = api_get("/api/market")
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected market response type: {type(data)}")
    return data


def fmt_vee(x):
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "0.00"


def fmt_pct(x):
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.2f}%"
    except Exception:
        return "n/a"


# === LOGIKA MM ==============================================================


def build_suggestions(vee_usd, lp_pairs):
    """
    Sugeruje:
      - EXIT jeśli net_effective_pct < MM_EXIT_PCT
      - ENTER/UP jeśli net_effective_pct >= MM_EXIT_PCT
    """
    suggestions = []

    total_lp_vee = 0.0
    for row in lp_pairs:
        v = float(row.get("lp_value_now_vee") or row.get("value_lp_vee") or 0.0)
        total_lp_vee += v

    logging.info(
        f"[MM-DRY] vee_usd_price: {vee_usd:.7f}" if vee_usd else "[MM-DRY] vee_usd_price: n/a"
    )
    logging.info(f"[MM-DRY] lp pairs tracked: {len(lp_pairs)}")
    logging.info(f"[MM-DRY] current total LP value: {fmt_vee(total_lp_vee)} VEE")
    logging.info("")

    for row in lp_pairs:
        pair_addr = row.get("pair_address")
        name = row.get("item_name") or pair_addr
        net = row.get("net_effective_pct")
        try:
            net_f = float(net) if net is not None else None
        except Exception:
            net_f = None

        cur_lp_vee = float(
            row.get("lp_value_now_vee")
            or row.get("value_lp_vee")
            or 0.0
        )

        if net_f is None:
            continue

        # EXIT
        if net_f < MM_EXIT_PCT:
            suggestions.append(
                f"[EXIT] {name} ({pair_addr}) | "
                f"current LP: {fmt_vee(cur_lp_vee)} VEE | "
                f"net: {fmt_pct(net_f)} | "
                f"reason: net {net_f:.2f} < exit {MM_EXIT_PCT:.2f}"
            )
            continue

        # SCALE-UP / ENTER
        suggestions.append(
            f"[ENTER/UP] {name} ({pair_addr}) | "
            f"current LP: {fmt_vee(cur_lp_vee)} VEE -> +{fmt_vee(MM_ENTER_SIZE_VEE)} VEE | "
            f"net: {fmt_pct(net_f)}"
        )

    return suggestions


def discover_new_pairs(lp_pairs, market_pairs):
    """
    Szukanie par, w których NIE masz LP,
    ale mają sensowny volume + rezerwy.

    Nic nie robi na chainie, tylko loguje.
    """
    user_pairs = {row.get("pair_address", "").lower() for row in lp_pairs if row.get("pair_address")}

    candidates = []

    for m in market_pairs:
        addr = (m.get("pair_address") or "").lower()
        if not addr or addr in user_pairs:
            continue

        vol24 = float(m.get("volume_24h_vee") or 0.0)
        reserve_vee = float(m.get("reserve_vee") or 0.0)
        name = m.get("item_name") or addr

        if vol24 < DISCOVER_MIN_VOL24:
            continue
        if reserve_vee < DISCOVER_MIN_RESERVE:
            continue

        candidates.append(
            {
                "pair_address": m.get("pair_address"),
                "item_name": name,
                "volume_24h_vee": vol24,
                "reserve_vee": reserve_vee,
            }
        )

    # sortuj po volume malejąco
    candidates.sort(key=lambda r: r["volume_24h_vee"], reverse=True)
    return candidates


def one_tick():
    wallet = MM_WALLET
    logging.info("=" * 80)
    logging.info(f"[MM-DRY] Fetching data for wallet: {wallet}")
    logging.info(f"[MM-DRY] API base: {API_BASE}")
    logging.info("-" * 80)

    # 1) IL + net_effective_pct dla TWOICH LP
    vee_usd, lp_pairs = fetch_wallet_il(wallet)

    # 2) Sugestie EXIT / ENTER dla istniejących LP
    suggestions = build_suggestions(vee_usd, lp_pairs)

    logging.info(f"[MM-DRY] Suggested actions ({len(suggestions)}):")
    if suggestions:
        logging.info("-" * 80)
        for line in suggestions:
            logging.info(line)
        logging.info("-" * 80)
    else:
        logging.info("[MM-DRY] No actions suggested.")

    # 3) Skan całego rynku (tylko volume / reserve) dla DISCOVER
    try:
        market_pairs = fetch_market_all()
        discover = discover_new_pairs(lp_pairs, market_pairs)
        logging.info(f"[MM-DRY] Discover candidates (not in your LP): {len(discover)}")
        for d in discover:
            logging.info(
                f"[DISCOVER] {d['item_name']} ({d['pair_address']}) | "
                f"vol24: {fmt_vee(d['volume_24h_vee'])} VEE | "
                f"reserve_vee: {fmt_vee(d['reserve_vee'])} VEE"
            )
    except Exception as e:
        logging.error(f"[MM-DRY] Error during market discover: {e}")

    logging.info("[MM-DRY] End of tick.")


# === MAIN ====================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="Run in loop")
    parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Seconds between ticks in loop mode",
    )
    args = parser.parse_args()

    if not MM_WALLET:
        logging.error("MM_WALLET not set in env/.env")
        return

    if args.loop:
        while True:
            try:
                one_tick()
            except Exception as e:
                logging.error(f"FATAL in tick: {e}")
            time.sleep(args.interval)
    else:
        one_tick()


if __name__ == "__main__":
    main()
