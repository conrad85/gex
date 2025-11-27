#!/usr/bin/env python3
import os
import time
import json
import argparse
from typing import Dict, Any, List

import requests
from dotenv import load_dotenv

import logging
from logging.handlers import RotatingFileHandler

# ===================== LOAD CONFIG =====================

load_dotenv()

# Log file path from env or default
LOG_PATH = os.getenv("MM_LOG_PATH", "mm_bot.log")

# ===================== LOGGING SETUP =====================

logger = logging.getLogger("mm_bot")
logger.setLevel(logging.INFO)

handler = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5)
formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)
logger.addHandler(handler)


def log(msg: str):
    """Log to both console and file."""
    print(msg)
    logger.info(msg)


# ===================== CONFIG DICT =====================

CONFIG = {
    "wallet_address": os.getenv(
        "MM_WALLET",
        "0x2aEb84d9b061C850B1F3C8C5200BaE14270D49f0",
    ),

    "api_base": os.getenv("MM_API_BASE", "http://127.0.0.1:8000"),

    # MM thresholds
    "min_volume_24h_vee": float(os.getenv("MM_MIN_VOL_24H_VEE", "0")),
    "min_reserve_vee": float(os.getenv("MM_MIN_RESERVE_VEE", "0")),
    "entry_net_effective_min": float(os.getenv("MM_ENTRY_NET_MIN", "20.0")),
    "exit_net_effective_max": float(os.getenv("MM_EXIT_NET_MAX", "2.0")),

    # LP caps
    "max_lp_value_vee_total": float(os.getenv("MM_MAX_LP_TOTAL_VEE", "1200000")),
    "max_lp_per_pair_vee": float(os.getenv("MM_MAX_LP_PER_PAIR_VEE", "300000")),
    "chunk_add_vee": float(os.getenv("MM_CHUNK_ADD_VEE", "30000")),

    # Allowlist
    "target_pairs_allowlist": json.loads(
        os.getenv("MM_TARGET_ALLOWLIST_JSON", "[]")
    ),
}


# ===================== HELPERS =====================

def api_get(path: str) -> Any:
    base = CONFIG["api_base"].rstrip("/")
    url = f"{base}{path}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_il(wallet: str) -> Dict[str, Any]:
    return api_get(f"/api/lp/{wallet}/il")


def fetch_market() -> List[Dict[str, Any]]:
    return api_get("/api/market")


def fmt_vee(x):
    try:
        return f"{float(x):,.2f} VEE"
    except:
        return "-"


def fmt_pct(x):
    try:
        return f"{float(x):.2f}%"
    except:
        return "-"


# ===================== STRATEGY LOGIC =====================

def pick_candidates(il_data, market_data, cfg):
    allowlist = [p.lower() for p in cfg["target_pairs_allowlist"]]
    min_vol = cfg["min_volume_24h_vee"]
    min_reserve = cfg["min_reserve_vee"]
    net_min = cfg["entry_net_effective_min"]

    market_by_pair = {
        row["pair_address"].lower(): row
        for row in market_data
        if row.get("pair_address")
    }

    candidates = []

    for row in il_data.get("pairs", []):
        pair = row.get("pair_address")
        if not pair:
            continue
        pl = pair.lower()

        if allowlist and pl not in allowlist:
            continue

        m = market_by_pair.get(pl)
        if not m:
            continue

        vol24 = float(m.get("volume_24h_vee") or 0.0)
        reserve_vee = float(m.get("reserve_vee") or 0.0)
        net = row.get("net_effective_pct")

        if vol24 < min_vol:
            continue
        if reserve_vee < min_reserve:
            continue
        if net is None or net < net_min:
            continue

        candidates.append(
            {
                "pair_address": pair,
                "item_name": row.get("item_name"),
                "net_effective_pct": net,
                "value_lp_vee": float(row.get("value_lp_vee") or 0.0),
                "volume_24h_vee": vol24,
                "reserve_vee": reserve_vee,
            }
        )

    candidates.sort(key=lambda x: x["net_effective_pct"], reverse=True)
    return candidates


def compute_actions(il_data, market_data, cfg):
    actions = []

    exit_threshold = cfg["exit_net_effective_max"]
    max_total_lp = cfg["max_lp_value_vee_total"]
    max_lp_per_pair = cfg["max_lp_per_pair_vee"]
    chunk_add = cfg["chunk_add_vee"]

    pairs_il = {
        p["pair_address"].lower(): p
        for p in il_data.get("pairs", [])
    }

    total_lp_now = sum(
        float(p.get("value_lp_vee") or 0.0)
        for p in il_data.get("pairs", [])
    )

    # EXIT
    for pl, row in pairs_il.items():
        net = row.get("net_effective_pct")
        current_lp = float(row.get("value_lp_vee") or 0.0)
        if current_lp > 0 and net is not None and net < exit_threshold:
            actions.append(
                {
                    "type": "exit",
                    "pair_address": row["pair_address"],
                    "item_name": row.get("item_name"),
                    "current_lp_vee": current_lp,
                    "net_effective_pct": net,
                    "reason": f"net {net:.2f} < exit {exit_threshold:.2f}",
                }
            )

    # ENTER / UP
    candidates = pick_candidates(il_data, market_data, cfg)

    for c in candidates:
        pl = c["pair_address"].lower()
        current_lp = float(pairs_il.get(pl, {}).get("value_lp_vee") or 0.0)

        if total_lp_now >= max_total_lp:
            break

        room = max_lp_per_pair - current_lp
        if room <= 0:
            continue

        add_vee = min(room, chunk_add, max_total_lp - total_lp_now)
        if add_vee <= 0:
            continue

        actions.append(
            {
                "type": "enter_or_increase",
                "pair_address": c["pair_address"],
                "item_name": c.get("item_name"),
                "add_lp_vee": add_vee,
                "current_lp_vee": current_lp,
                "net_effective_pct": c["net_effective_pct"],
                "volume_24h_vee": c["volume_24h_vee"],
                "reserve_vee": c["reserve_vee"],
                "reason": (
                    f"net {c['net_effective_pct']:.2f}, "
                    f"vol24 {c['volume_24h_vee']:.0f}, "
                    f"reserve_vee {c['reserve_vee']:.0f}"
                ),
            }
        )

        total_lp_now += add_vee

    return actions


# ===================== MAIN LOOP =====================

def dry_run_once():
    wallet = CONFIG["wallet_address"]
    log("=" * 80)
    log(f"[MM-DRY] Fetching data for wallet: {wallet}")
    log(f"[MM-DRY] API base: {CONFIG['api_base']}")
    log("-" * 80)

    try:
        il_data = fetch_il(wallet)
        market_data = fetch_market()
    except Exception as e:
        log(f"[MM-DRY] ERROR fetching data: {repr(e)}")
        return

    pairs = il_data.get("pairs", [])
    vee_usd = il_data.get("vee_usd_price")
    log(f"[MM-DRY] vee_usd_price: {vee_usd}")
    log(f"[MM-DRY] lp pairs tracked: {len(pairs)}")

    total_lp_vee = sum(float(p.get("value_lp_vee") or 0.0) for p in pairs)
    log(f"[MM-DRY] current total LP value: {fmt_vee(total_lp_vee)}")
    log("")

    actions = compute_actions(il_data, market_data, CONFIG)

    if not actions:
        log("[MM-DRY] No actions suggested for this tick.")
        return

    log(f"[MM-DRY] Suggested actions ({len(actions)}):")
    log("-" * 80)

    for a in actions:
        t = a["type"]
        name = a.get("item_name") or "?"
        pair = a["pair_address"]

        if t == "exit":
            log(
                f"[EXIT] {name} ({pair}) | current LP: {fmt_vee(a['current_lp_vee'])} "
                f"| net: {fmt_pct(a['net_effective_pct'])} | reason: {a['reason']}"
            )

        elif t == "enter_or_increase":
            log(
                f"[ENTER/UP] {name} ({pair}) | current LP: {fmt_vee(a['current_lp_vee'])} "
                f"-> +{fmt_vee(a['add_lp_vee'])} | net: {fmt_pct(a['net_effective_pct'])} "
                f"| vol24: {a['volume_24h_vee']:.0f} | reserve_vee: {a['reserve_vee']:.0f} "
                f"| reason: {a['reason']}"
            )

    log("-" * 80)
    log("[MM-DRY] End of tick.")


def main():
    parser = argparse.ArgumentParser(description="Neutral MM DRY-RUN bot with logging")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously every N seconds",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("MM_LOOP_INTERVAL_SEC", "600")),
        help="Interval in seconds (default 600)",
    )

    args = parser.parse_args()

    if not args.loop:
        dry_run_once()
        return

    log(f"[MM-DRY] Starting loop, interval = {args.interval} sec")
    while True:
        try:
            dry_run_once()
        except Exception as e:
            log(f"[MM-DRY] Unhandled error in loop: {repr(e)}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
