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
    Pobiera ostatni snapshot każdej pary z gex_snapshots oraz wolumen 24h z trades_ronin.
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
    vol AS (
        SELECT
            LOWER(pair_address) AS pair_lower,
            COALESCE(SUM(vee_amount), 0) AS volume_24h_vee,
            COUNT(*) AS trades_24h
        FROM trades_ronin
        WHERE ts >= NOW() - INTERVAL '24 hours'
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
        COALESCE(v.volume_24h_vee, 0) AS volume_24h_vee,
        COALESCE(v.volume_24h_vee, 0) AS volume_24h_est,
        COALESCE(v.trades_24h, 0) AS volume_24h_trades
    FROM latest l
    LEFT JOIN vol v ON v.pair_lower = l.pair_lower;
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

        try:
            lp_balance, lp_share = get_lp_info(row["pair_address"], wallet_checksum)
            reserve_item = float(row["reserve_item"] or 0)
            reserve_vee = float(row["reserve_vee"] or 0)

            row["lp_balance"] = lp_balance
            row["lp_share"] = lp_share
            row["user_item"] = lp_share * reserve_item
            row["user_vee"] = lp_share * reserve_vee

        except Exception:
            continue

    return data


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
