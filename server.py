from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import os
import time
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import json
import traceback
from datetime import datetime

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    print("UNCAUGHT ERROR:", repr(exc))
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": str(exc)})


DB_PARAMS = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

# Szacowane fee od wolumenu, które trafia do LP (np. 5% -> 0.05)
LP_FEE_RATE = float(os.getenv("LP_FEE_RATE", "0.05"))

# Opcjonalna cena VEE w USD (do przeliczeń IL na USD)
VEE_USD_PRICE = float(os.getenv("VEE_USD", "0") or "0")

# RPC (HTTP)
RPC_DEFAULT = "https://ronin-mainnet.g.alchemy.com/v2/IJPvvQ6YdcbcF85OD8jNsjBrpGo3-Xh0"
RPC_RAW = os.getenv("RONIN_RPC", RPC_DEFAULT)
if RPC_RAW.startswith("wss://"):
    RPC_HTTP = "https://" + RPC_RAW.removeprefix("wss://")
else:
    RPC_HTTP = RPC_RAW

w3 = Web3(Web3.HTTPProvider(RPC_HTTP))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

ABI_ERC20 = json.loads(
    """
[
  {
    "constant": true,
    "inputs": [],
    "name": "totalSupply",
    "outputs": [
      { "name": "", "type": "uint256" }
    ],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "constant": true,
    "inputs": [
      { "name": "owner", "type": "address" }
    ],
    "name": "balanceOf",
    "outputs": [
      { "name": "", "type": "uint256" }
    ],
    "stateMutability": "view",
    "type": "function"
  }
]
"""
)

LP_CACHE = {}
LP_CACHE_TTL = 300  # seconds


# ================== CORE SNAPSHOTS ==================


def query_latest():
    """
    Pobiera ostatni snapshot każdej pary z gex_snapshots
    oraz wolumen 24h i 7 dni z trades_ronin + zmiany ceny/vol w 24h/7d.
    """
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    query = """
    WITH latest AS (
        SELECT DISTINCT ON (pair_address)
            pair_address,
            LOWER(pair_address) AS pair_lower,
            item_name,
            price_vee,
            reserve_vee,
            reserve_item,
            vee_address,
            item_address,
            ts
        FROM gex_snapshots
        ORDER BY pair_address, ts DESC
    ),
    vol24 AS (
        SELECT
            LOWER(pair_address) AS pair_lower,
            COALESCE(SUM(vee_amount), 0) AS volume_24h_vee,
            COUNT(*) AS trades_24h
        FROM trades_ronin
        WHERE ts >= NOW() - INTERVAL '24 hours'
        GROUP BY LOWER(pair_address)
    ),
    vol7 AS (
        SELECT
            LOWER(pair_address) AS pair_lower,
            COALESCE(SUM(vee_amount), 0) AS volume_7d_vee,
            COUNT(*) AS trades_7d
        FROM trades_ronin
        WHERE ts >= NOW() - INTERVAL '7 days'
        GROUP BY LOWER(pair_address)
    ),
    price24 AS (
        SELECT DISTINCT ON (pair_address)
            pair_address,
            LOWER(pair_address) AS pair_lower,
            price_vee AS price_24h_ago
        FROM gex_snapshots
        WHERE ts <= NOW() - INTERVAL '24 hours'
        ORDER BY pair_address, ts DESC
    ),
    price7 AS (
        SELECT DISTINCT ON (pair_address)
            pair_address,
            LOWER(pair_address) AS pair_lower,
            price_vee AS price_7d_ago
        FROM gex_snapshots
        WHERE ts <= NOW() - INTERVAL '7 days'
        ORDER BY pair_address, ts DESC
    ),
    vol24_prev AS (
        SELECT
            LOWER(pair_address) AS pair_lower,
            COALESCE(SUM(vee_amount), 0) AS volume_24h_prev_vee
        FROM trades_ronin
        WHERE ts >= NOW() - INTERVAL '48 hours'
          AND ts <  NOW() - INTERVAL '24 hours'
        GROUP BY LOWER(pair_address)
    ),
    vol7_prev AS (
        SELECT
            LOWER(pair_address) AS pair_lower,
            COALESCE(SUM(vee_amount), 0) AS volume_7d_prev_vee
        FROM trades_ronin
        WHERE ts >= NOW() - INTERVAL '14 days'
          AND ts <  NOW() - INTERVAL '7 days'
        GROUP BY LOWER(pair_address)
    )
    SELECT
        l.pair_address,
        l.item_name,
        l.price_vee,
        l.reserve_vee,
        l.reserve_item,
        l.vee_address,
        l.item_address,
        l.ts,
        COALESCE(v24.volume_24h_vee, 0)     AS volume_24h_vee,
        COALESCE(v24.trades_24h, 0)         AS volume_24h_trades,
        COALESCE(v7.volume_7d_vee, 0)       AS volume_7d_vee,
        COALESCE(v7.trades_7d, 0)           AS volume_7d_trades,
        p24.price_24h_ago,
        p7.price_7d_ago,
        COALESCE(
            CASE
                WHEN p24.price_24h_ago IS NULL OR p24.price_24h_ago = 0 THEN NULL
                ELSE ((l.price_vee - p24.price_24h_ago) / p24.price_24h_ago) * 100
            END, 0
        ) AS price_change_24h_pct,
        COALESCE(
            CASE
                WHEN p7.price_7d_ago IS NULL OR p7.price_7d_ago = 0 THEN NULL
                ELSE ((l.price_vee - p7.price_7d_ago) / p7.price_7d_ago) * 100
            END, 0
        ) AS price_change_7d_pct,
        COALESCE(v24_prev.volume_24h_prev_vee, 0) AS volume_24h_prev_vee,
        COALESCE(v7_prev.volume_7d_prev_vee, 0)   AS volume_7d_prev_vee,
        CASE
            WHEN v24_prev.volume_24h_prev_vee IS NULL
                 OR v24_prev.volume_24h_prev_vee = 0 THEN NULL
            ELSE ( (COALESCE(v24.volume_24h_vee, 0) - v24_prev.volume_24h_prev_vee)
                   / v24_prev.volume_24h_prev_vee ) * 100
        END AS volume_change_24h_pct,
        CASE
            WHEN v7_prev.volume_7d_prev_vee IS NULL
                 OR v7_prev.volume_7d_prev_vee = 0 THEN NULL
            ELSE ( (COALESCE(v7.volume_7d_vee, 0) - v7_prev.volume_7d_prev_vee)
                   / v7_prev.volume_7d_prev_vee ) * 100
        END AS volume_change_7d_pct
    FROM latest    l
    LEFT JOIN vol24       v24      ON v24.pair_lower      = l.pair_lower
    LEFT JOIN vol7        v7       ON v7.pair_lower       = l.pair_lower
    LEFT JOIN price24     p24      ON p24.pair_lower      = l.pair_lower
    LEFT JOIN price7      p7       ON p7.pair_lower       = l.pair_lower
    LEFT JOIN vol24_prev  v24_prev ON v24_prev.pair_lower = l.pair_lower
    LEFT JOIN vol7_prev   v7_prev  ON v7_prev.pair_lower  = l.pair_lower;
    """

    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    columns = [
        "pair_address",
        "item_name",
        "price_vee",
        "reserve_vee",
        "reserve_item",
        "vee_address",
        "item_address",
        "ts",
        "volume_24h_vee",
        "volume_24h_trades",
        "volume_7d_vee",
        "volume_7d_trades",
        "price_24h_ago",
        "price_7d_ago",
        "price_change_24h_pct",
        "price_change_7d_pct",
        "volume_24h_prev_vee",
        "volume_7d_prev_vee",
        "volume_change_24h_pct",
        "volume_change_7d_pct",
    ]

    return [dict(zip(columns, row)) for row in rows]


# ================== LP HELPERS ==================


def get_lp_info(pair_address: str, wallet_checksum: str):
    """
    Zwraca (lp_balance, lp_share) z cache lub RPC. Cache per wallet+pair na LP_CACHE_TTL.
    """
    key = (wallet_checksum.lower(), pair_address.lower())
    now = time.time()

    cached = LP_CACHE.get(key)
    if cached and now - cached["ts"] < LP_CACHE_TTL:
        return cached["lp_balance"], cached["lp_share"]

    try:
        pair_checksum = w3.to_checksum_address(pair_address)
        contract = w3.eth.contract(address=pair_checksum, abi=ABI_ERC20)

        total = contract.functions.totalSupply().call()
        bal = contract.functions.balanceOf(wallet_checksum).call()

        total_f = total / 1e18 if total > 0 else 0.0
        bal_f = bal / 1e18 if bal > 0 else 0.0
        if total_f <= 0:
            LP_CACHE[key] = {"ts": now, "lp_balance": 0.0, "lp_share": 0.0}
            return 0.0, 0.0

        share = bal_f / total_f
        LP_CACHE[key] = {"ts": now, "lp_balance": bal_f, "lp_share": share}
        return bal_f, share
    except Exception:
        return 0.0, 0.0


def query_lp_latest(wallet: str):
    """
    Ostatni snapshot z lp_snapshots dla każdej pary danego walleta.
    """
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT ON (pair_address)
            pair_address,
            item_name,
            ts,
            price_vee,
            reserve_vee,
            reserve_item,
            lp_balance,
            lp_share,
            user_vee,
            user_item,
            volume_24h_vee,
            volume_7d_vee,
            lp_earn_vee_24h,
            lp_earn_vee_7d,
            lp_apr
        FROM lp_snapshots
        WHERE LOWER(wallet_address) = LOWER(%s)
        ORDER BY pair_address, ts DESC
        """,
        (wallet,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    cols = [
        "pair_address",
        "item_name",
        "ts",
        "price_vee",
        "reserve_vee",
        "reserve_item",
        "lp_balance",
        "lp_share",
        "user_vee",
        "user_item",
        "volume_24h_vee",
        "volume_7d_vee",
        "lp_earn_vee_24h",
        "lp_earn_vee_7d",
        "lp_apr",
    ]
    result = []
    for r in rows:
        d = dict(zip(cols, r))
        for k in [
            "price_vee",
            "reserve_vee",
            "reserve_item",
            "lp_balance",
            "lp_share",
            "user_vee",
            "user_item",
            "volume_24h_vee",
            "volume_7d_vee",
            "lp_earn_vee_24h",
            "lp_earn_vee_7d",
            "lp_apr",
        ]:
            if k in d and d[k] is not None:
                d[k] = float(d[k])
        if isinstance(d.get("ts"), datetime):
            d["ts"] = d["ts"].isoformat()
        result.append(d)

    return result


def query_lp_history(wallet: str):
    """
    Pełna historia LP dla walleta – pod IL.
    Zostawiamy ts jako datetime (bez isoformat), bo liczymy różnicę czasową.
    """
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            pair_address,
            item_name,
            ts,
            price_vee,
            user_vee,
            user_item,
            lp_apr
        FROM lp_snapshots
        WHERE LOWER(wallet_address) = LOWER(%s)
        ORDER BY pair_address, ts ASC
        """,
        (wallet,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    cols = [
        "pair_address",
        "item_name",
        "ts",
        "price_vee",
        "user_vee",
        "user_item",
        "lp_apr",
    ]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        for k in ["price_vee", "user_vee", "user_item", "lp_apr"]:
            if k in d and d[k] is not None:
                d[k] = float(d[k])
        # ts zostaje datetime
        out.append(d)
    return out


def calc_il(entry_vee, entry_item, cur_vee, cur_item, price_vee):
    """
    IL liczony w VEE:
    - value_hodl = (entry_vee + entry_item * price_now)
    - value_lp   = (cur_vee   + cur_item   * price_now)
    """
    if price_vee is None:
        return 0.0, 0.0, 0.0, 0.0

    entry_vee = float(entry_vee or 0.0)
    entry_item = float(entry_item or 0.0)
    cur_vee = float(cur_vee or 0.0)
    cur_item = float(cur_item or 0.0)
    price_vee = float(price_vee or 0.0)

    value_hodl = entry_vee + entry_item * price_vee
    value_lp = cur_vee + cur_item * price_vee

    if value_hodl <= 0:
        return 0.0, 0.0, value_hodl, value_lp

    il = value_lp - value_hodl
    il_pct = (il / value_hodl) * 100.0
    return il, il_pct, value_hodl, value_lp


def compute_lp_il_for_wallet(wallet: str):
    """
    Zwraca IL per para + prosty scoring "opłacalności":
    net_effective_pct = lp_apr (z ostatniego snapshotu) + IL annualized.
    """
    history = query_lp_history(wallet)
    if not history:
        return []

    per_pair = {}
    for row in history:
        key = row["pair_address"].lower()
        per_pair.setdefault(key, []).append(row)

    results = []
    total_positive_score = 0.0

    for key, rows in per_pair.items():
        if not rows:
            continue

        rows_sorted = sorted(rows, key=lambda r: r["ts"])
        entry = rows_sorted[0]
        current = rows_sorted[-1]

        entry_vee = entry.get("user_vee") or 0.0
        entry_item = entry.get("user_item") or 0.0
        cur_vee = current.get("user_vee") or 0.0
        cur_item = current.get("user_item") or 0.0

        price_now = current.get("price_vee") or 0.0
        il_vee, il_pct, value_hodl, value_lp = calc_il(
            entry_vee, entry_item, cur_vee, cur_item, price_now
        )

        try:
            t0 = entry["ts"]
            t1 = current["ts"]
            delta_days = max((t1 - t0).total_seconds() / 86400.0, 0.0)
        except Exception:
            delta_days = 0.0

        if delta_days <= 0 or il_pct is None:
            il_annualized_pct = None
        else:
            il_annualized_pct = il_pct * (365.0 / max(delta_days, 1e-6))

        lp_apr = current.get("lp_apr")
        if lp_apr is not None:
            lp_apr = float(lp_apr)

        net_effective_pct = None
        if lp_apr is not None and il_annualized_pct is not None:
            net_effective_pct = lp_apr + il_annualized_pct
        elif lp_apr is not None:
            net_effective_pct = lp_apr

        il_usd = None
        value_hodl_usd = None
        value_lp_usd = None
        if VEE_USD_PRICE > 0:
            il_usd = il_vee * VEE_USD_PRICE
            value_hodl_usd = value_hodl * VEE_USD_PRICE
            value_lp_usd = value_lp * VEE_USD_PRICE

        results.append(
            {
                "pair_address": current["pair_address"],
                "item_name": current.get("item_name"),
                "entry_ts": entry["ts"].isoformat() if isinstance(entry["ts"], datetime) else entry["ts"],
                "current_ts": current["ts"].isoformat() if isinstance(current["ts"], datetime) else current["ts"],
                "days_in_position": delta_days,
                "entry_user_vee": entry_vee,
                "entry_user_item": entry_item,
                "current_user_vee": cur_vee,
                "current_user_item": cur_item,
                "price_vee_now": price_now,
                "value_hodl_vee": value_hodl,
                "value_lp_vee": value_lp,
                "il_vee": il_vee,
                "il_pct": il_pct,
                "il_annualized_pct": il_annualized_pct,
                "lp_apr": lp_apr,
                "net_effective_pct": net_effective_pct,
                "il_usd": il_usd,
                "value_hodl_usd": value_hodl_usd,
                "value_lp_usd": value_lp_usd,
            }
        )

    positive = [
        r
        for r in results
        if r["net_effective_pct"] is not None and r["net_effective_pct"] > 0
    ]
    total_score = (
        sum(r["net_effective_pct"] for r in positive) if positive else 0.0
    )

    for r in results:
        if total_score > 0 and r in positive:
            r["target_weight"] = r["net_effective_pct"] / total_score
        else:
            r["target_weight"] = 0.0

    results.sort(
        key=lambda r: (
            r["net_effective_pct"] is None,
            -(r["net_effective_pct"] or -1e9),
        )
    )
    return results


# ================== ROUTES ==================


@app.get("/api/market")
def get_latest_snapshots_with_volume():
    rows = query_latest()
    return rows


@app.get("/api/market/{wallet}")
def get_latest_snapshots_with_volume_and_lp(wallet: str):
    """
    Market + LP:
    - market z gex_snapshots (query_latest)
    - LP z tabeli lp_cache (update co 10 min)
    Brak RPC przy każdym wejściu na UI.
    """
    data = query_latest()

    # Wczytujemy cache LP z bazy
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute("""
        SELECT LOWER(pair_address) AS pair_lower,
               lp_balance, lp_share, user_vee, user_item
        FROM lp_cache
    """)
    cache_rows = cur.fetchall()
    cur.close()
    conn.close()

    cache = {
        row[0]: {
            "lp_balance": float(row[1] or 0.0),
            "lp_share": float(row[2] or 0.0),
            "user_vee": float(row[3] or 0.0),
            "user_item": float(row[4] or 0.0),
        }
        for row in cache_rows
    }

    for row in data:
        key = row["pair_address"].lower()
        lp = cache.get(key)

        if lp:
            row["lp_balance"] = lp["lp_balance"]
            row["lp_share"] = lp["lp_share"]
            row["user_vee"] = lp["user_vee"]
            row["user_item"] = lp["user_item"]
        else:
            row["lp_balance"] = 0.0
            row["lp_share"] = 0.0
            row["user_vee"] = 0.0
            row["user_item"] = 0.0

        # Na razie fee earnings 24h/7d = 0 (możemy potem dorobić z trades_ronin)
        row["lp_earn_vee_24h"] = 0.0
        row["lp_earn_vee_7d"] = 0.0

    return data



@app.get("/api/history/{pair_address}")
def get_pair_history(pair_address: str):
    """
    Zwraca historię ceny + rezerw dla pary oraz dzienny wolumen z trades_ronin.
    """
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT ts, price_vee, reserve_vee, reserve_item
        FROM gex_snapshots
        WHERE LOWER(pair_address) = LOWER(%s)
        ORDER BY ts ASC
        """,
        (pair_address,),
    )
    snap_rows = cur.fetchall()
    snapshots = [
        {
            "ts": row[0].isoformat(),
            "price_vee": float(row[1]) if row[1] is not None else None,
            "reserve_vee": float(row[2]) if row[2] is not None else None,
            "reserve_item": float(row[3]) if row[3] is not None else None,
        }
        for row in snap_rows
    ]

    cur.execute(
        """
        SELECT date_trunc('day', ts) AS day, SUM(vee_amount) AS volume_vee
        FROM trades_ronin
        WHERE LOWER(pair_address) = LOWER(%s)
        GROUP BY 1
        ORDER BY 1
        """,
        (pair_address,),
    )
    vol_rows = cur.fetchall()
    volumes = [
        {
            "day": row[0].date().isoformat(),
            "volume_vee": float(row[1]) if row[1] is not None else 0.0,
        }
        for row in vol_rows
    ]

    cur.close()
    conn.close()

    return {"snapshots": snapshots, "daily_volume": volumes}


@app.get("/api/lp/{wallet}")
def get_lp_latest(wallet: str):
    """
    Ostatnie snapshoty LP z lp_snapshots (per para).
    """
    return query_lp_latest(wallet)


@app.get("/api/lp/{wallet}/il")
def get_lp_il(wallet: str):
    """
    IL kalkulator + prosty scoring optymalności par:
    - il_vee, il_pct, il_annualized_pct
    - lp_apr
    - net_effective_pct (APR + annualizowany IL)
    - target_weight (propozycja wag kapitału między parami)
    """
    return compute_lp_il_for_wallet(wallet)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
