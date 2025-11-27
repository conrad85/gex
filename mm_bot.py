#!/usr/bin/env python3
import argparse
import logging
import os
import time
from typing import List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# === KONFIG =================================================================

MM_WALLET = os.getenv(
    "MM_WALLET",
    "0x2aEb84d9b061C850B1F3C8C5200BaE14270D49f0",
)

# API backend (MM_API_BASE preferred, fallback to API_BASE or default localhost)
API_BASE = os.getenv("MM_API_BASE") or os.getenv("API_BASE", "http://127.0.0.1:8000")

# progi strategii
MM_EXIT_PCT = float(os.getenv("MM_EXIT_PCT") or os.getenv("MM_EXIT_NET_MAX", "2.0"))
MM_ENTER_SIZE_VEE = float(
    os.getenv("MM_ENTER_SIZE_VEE") or os.getenv("MM_CHUNK_ADD_VEE", "10000")
)
FEE_RATE = float(os.getenv("LP_FEE_RATE", "0.05"))

# progi dla DISCOVER (par, w ktorych nie masz LP)
DISCOVER_MIN_VOL24 = float(os.getenv("MM_DISCOVER_MIN_VOL24", "1000"))   # VEE
DISCOVER_MIN_RESERVE = float(os.getenv("MM_DISCOVER_MIN_RESERVE", "10000"))  # VEE
MM_DISCOVER_MIN_APR = float(os.getenv("MM_DISCOVER_MIN_APR", "0"))
MM_DISCOVER_TOP_N = int(os.getenv("MM_DISCOVER_TOP_N", "5"))

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
logger = logging.getLogger("mm_bot")


# === POMOCNICZE =============================================================


def api_get(path: str):
    url = f"{API_BASE}{path}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_wallet_il(wallet: str) -> Tuple[float, List[dict]]:
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
    Zwraca liste wszystkich par z:
    - pair_address
    - item_name
    - volume_24h_vee
    - reserve_vee
    - itd.
    """
    data = api_get("/api/market")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("pairs", [])
    return []


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
      - EXIT jezeli net_effective_pct < MM_EXIT_PCT
      - ENTER/UP jezeli net_effective_pct >= MM_EXIT_PCT
    """
    suggestions = []

    total_lp_vee = 0.0
    for row in lp_pairs:
        v = float(row.get("lp_value_now_vee") or row.get("value_lp_vee") or 0.0)
        total_lp_vee += v

    logger.info(
        f"[MM] vee_usd_price: {vee_usd:.7f}" if vee_usd else "[MM] vee_usd_price: n/a"
    )
    logger.info(f"[MM] lp pairs tracked: {len(lp_pairs)}")
    logger.info(f"[MM] current total LP value: {fmt_vee(total_lp_vee)} VEE")
    logger.info("")

    for row in lp_pairs:
        pair_addr = row.get("pair_address")
        name = row.get("item_name") or pair_addr
        net = row.get("net_effective_pct")
        try:
            net_f = float(net) if net is not None else None
        except Exception:
            net_f = None

        cur_lp_vee = float(
            row.get("lp_value_now_vee") or row.get("value_lp_vee") or 0.0
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


def discover_new_pairs(wallet: str):
    """
    Szuka par, w ktorych NIE masz LP, ale:
      - maja sensowny volume 24h,
      - maja sensowna rezerwe VEE,
      - maja sensowny 'pool APR' liczony z volume i FEE.
    """
    try:
        data = fetch_market_all()
        if not isinstance(data, list):
            logger.error("[MM] /api/market unexpected shape (not list)")
            return []

        candidates = []

        for row in data:
            try:
                pair = str(row.get("pair_address") or "").lower()
                if not pair:
                    continue

                item_name = row.get("item_name") or "?"

                lp_share = float(row.get("lp_share") or 0.0)
                # interesuja nas TYLKO pule, gdzie NIE masz LP
                if lp_share > 0:
                    continue

                vol24 = (
                    float(row.get("volume_24h_vee") or 0.0)
                    if row.get("volume_24h_vee") is not None
                    else float(row.get("volume_24h_est") or 0.0)
                )
                reserve_vee = float(row.get("reserve_vee") or 0.0)

                if vol24 <= 0 or reserve_vee <= 0:
                    continue

                # prosty pool-level APR:
                # dzienne fee = vol24 * FEE_RATE
                # wartosc puli ~ 2 * reserve_vee
                pool_apr_pct = (vol24 * FEE_RATE * 365.0) / (2.0 * reserve_vee) * 100.0

                # filtry progu
                if vol24 < DISCOVER_MIN_VOL24:
                    continue
                if reserve_vee < DISCOVER_MIN_RESERVE:
                    continue
                if pool_apr_pct < MM_DISCOVER_MIN_APR:
                    continue

                candidates.append(
                    {
                        "pair_address": pair,
                        "item_name": item_name,
                        "volume_24h_vee": vol24,
                        "reserve_vee": reserve_vee,
                        "pool_apr_pct": pool_apr_pct,
                    }
                )
            except Exception as inner_e:
                logger.warning("[MM] discover skip row error: %r row=%r", inner_e, row)
                continue

        # sortujemy po APR malejaco
        candidates.sort(key=lambda r: r["pool_apr_pct"], reverse=True)

        logger.info("[MM] Discover candidates (not in your LP): %d", len(candidates))
        for c in candidates[:MM_DISCOVER_TOP_N]:
            logger.info(
                "[DISCOVER] %s (%s) | pool_apr: %.2f%% | vol24: %.0f VEE | reserve_vee: %.0f",
                c["item_name"],
                c["pair_address"],
                c["pool_apr_pct"],
                c["volume_24h_vee"],
                c["reserve_vee"],
            )

        return candidates

    except Exception as e:
        logger.error("[MM] discover_new_pairs ERROR: %r", e)
        return []


def one_tick():
    logger.info(f"[MM] Fetching wallet LP: {MM_WALLET}")

    try:
        vee_usd, lp_pairs = fetch_wallet_il(MM_WALLET)
    except Exception as e:
        logger.error("[MM] Failed to fetch wallet IL: %r", e)
        return

    try:
        suggestions = build_suggestions(vee_usd, lp_pairs)
    except Exception as e:
        logger.error("[MM] Failed to build suggestions: %r", e)
        suggestions = []

    logger.info("[MM] Suggested actions (%d):", len(suggestions))
    for s in suggestions:
        logger.info(s)

    # discover nowe pary
    discover_new_pairs(MM_WALLET)

    logger.info("[MM] End of tick.")


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
        logger.error("MM_WALLET not set in env/.env")
        return

    if args.loop:
        while True:
            try:
                one_tick()
            except Exception as e:
                logger.error("FATAL in tick: %r", e)
            time.sleep(args.interval)
    else:
        one_tick()


if __name__ == "__main__":
    main()
