from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import os
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import json
import time
import traceback

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
        COALESCE(v24.volume_24h_vee, 0) AS volume_24h_vee,
        COALESCE(v24.volume_24h_vee, 0) AS volume_24h_est,
        COALESCE(v24.trades_24h, 0)       AS volume_24h_trades,
        COALESCE(v7.volume_7d_vee, 0)     AS volume_7d_vee,
        COALESCE(v7.trades_7d, 0)         AS volume_7d_trades,
        COALESCE(p24.price_24h_ago, 0)    AS price_24h_ago,
        COALESCE(p7.price_7d_ago, 0)      AS price_7d_ago,
        CASE
            WHEN p24.price_24h_ago IS NULL OR p24.price_24h_ago = 0 THEN NULL
            ELSE (l.price_vee - p24.price_24h_ago) / p24.price_24h_ago * 100
        END AS price_change_24h_pct,
        CASE
            WHEN p7.price_7d_ago IS NULL OR p7.price_7d_ago = 0 THEN NULL
            ELSE (l.price_vee - p7.price_7d_ago) / p7.price_7d_ago * 100
        END AS price_change_7d_pct,
        COALESCE(v24prev.volume_24h_prev_vee, 0) AS volume_24h_prev_vee,
        COALESCE(v7prev.volume_7d_prev_vee, 0)   AS volume_7d_prev_vee,
        CASE
            WHEN v24prev.volume_24h_prev_vee IS NULL OR v24prev.volume_24h_prev_vee = 0 THEN NULL
            ELSE
                (COALESCE(v24.volume_24h_vee, 0) - v24prev.volume_24h_prev_vee)
                / v24prev.volume_24h_prev_vee * 100
        END AS volume_change_24h_pct,
        CASE
            WHEN v7prev.volume_7d_prev_vee IS NULL OR v7prev.volume_7d_prev_vee = 0 THEN NULL
            ELSE
                (COALESCE(v7.volume_7d_vee, 0) - v7prev.volume_7d_prev_vee)
                / v7prev.volume_7d_prev_vee * 100
        END AS volume_change_7d_pct
    FROM latest l
    LEFT JOIN vol24      v24      ON v24.pair_lower      = l.pair_lower
    LEFT JOIN vol7       v7       ON v7.pair_lower       = l.pair_lower
    LEFT JOIN vol24_prev v24prev  ON v24prev.pair_lower  = l.pair_lower
    LEFT JOIN vol7_prev  v7prev   ON v7prev.pair_lower   = l.pair_lower
    LEFT JOIN price24    p24      ON p24.pair_lower      = l.pair_lower
    LEFT JOIN price7     p7       ON p7.pair_lower       = l.pair_lower;
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
        "volume_24h_est",
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


@app.get("/api/market")
def get_latest_snapshots_with_volume():
    rows = query_latest()
    return rows


@app.get("/api/market/{wallet}")
def get_latest_snapshots_with_volume_and_lp(wallet: str):
    data = query_latest()

    try:
        wallet_checksum = w3.to_checksum_address(wallet)
    except Exception:
        return data

    for row in data:
        row["lp_balance"] = 0.0
        row["lp_share"] = 0.0
        row["user_item"] = 0.0
        row["user_vee"] = 0.0
        row["lp_earn_vee_24h"] = 0.0
        row["lp_earn_vee_7d"] = 0.0

        try:
            lp_balance, lp_share = get_lp_info(row["pair_address"], wallet_checksum)
            reserve_item = float(row["reserve_item"] or 0)
            reserve_vee = float(row["reserve_vee"] or 0)

            row["lp_balance"] = lp_balance
            row["lp_share"] = lp_share
            row["user_item"] = lp_share * reserve_item
            row["user_vee"] = lp_share * reserve_vee

            # Szacowane zarobki LP w VEE (24h / 7d)
            vol24 = float(row.get("volume_24h_vee") or 0.0)
            vol7 = float(row.get("volume_7d_vee") or 0.0)

            if lp_share > 0 and (vol24 > 0 or vol7 > 0) and LP_FEE_RATE > 0:
                row["lp_earn_vee_24h"] = vol24 * LP_FEE_RATE * lp_share
                row["lp_earn_vee_7d"] = vol7 * LP_FEE_RATE * lp_share

        except Exception:
            continue

    return data

@app.get("/api/history/{pair_address}")
def get_pair_history(pair_address: str):
    """
    Zwraca historię ceny + rezerw dla pary oraz dzienny wolumen z trades_ronin.
    Idealne pod wykresy na stronie itemu.
    """
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    # historia snapshotów
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

    # dzienny wolumen z trades_ronin
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

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
