from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import os
from dotenv import load_dotenv
from web3 import Web3
from hexbytes import HexBytes
import json
import time
import traceback

# --- POA middleware (Ronin) ---
from web3.middleware import ExtraDataToPOAMiddleware

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
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
    )


DB_PARAMS = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

# === RPC: always HTTP ===
RPC_DEFAULT = "https://ronin-mainnet.g.alchemy.com/v2/IJPvvQ6YdcbcF85OD8jNsjBrpGo3-Xh0"
RPC_RAW = os.getenv("RONIN_RPC", RPC_DEFAULT)
if RPC_RAW.startswith("wss://"):
    RPC_HTTP = "https://" + RPC_RAW.removeprefix("wss://")
else:
    RPC_HTTP = RPC_RAW

w3 = Web3(Web3.HTTPProvider(RPC_HTTP))
# Ronin = POA (web3 v6+)
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

ABI_PAIR = json.loads(
    """
[
  {
    "constant": true,
    "inputs": [],
    "name": "token0",
    "outputs": [{ "name": "","type": "address" }],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "constant": true,
    "inputs": [],
    "name": "token1",
    "outputs": [{ "name": "","type": "address" }],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "anonymous": false,
    "inputs": [
      { "indexed": true, "internalType": "address", "name": "sender", "type": "address" },
      { "indexed": false, "internalType": "uint256", "name": "amount0In", "type": "uint256" },
      { "indexed": false, "internalType": "uint256", "name": "amount1In", "type": "uint256" },
      { "indexed": false, "internalType": "uint256", "name": "amount0Out", "type": "uint256" },
      { "indexed": false, "internalType": "uint256", "name": "amount1Out", "type": "uint256" },
      { "indexed": true, "internalType": "address", "name": "to", "type": "address" }
    ],
    "name": "Swap",
    "type": "event"
  }
]
"""
)

VEE_DECIMALS = 18

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


def get_block_timestamp(block_number: int) -> int:
    block = w3.eth.get_block(block_number)
    return int(block["timestamp"])


def estimate_blocks_per_day():
    """
    Szacuje ile bloków jest w 24h na podstawie ostatnich ~200 bloków.
    """
    latest_block = w3.eth.block_number
    if latest_block == 0:
        return 0, latest_block, int(time.time())

    sample_block = max(latest_block - 200, 0)
    ts_latest = get_block_timestamp(latest_block)
    ts_sample = get_block_timestamp(sample_block)

    diff_blocks = latest_block - sample_block or 1
    diff_time = max(ts_latest - ts_sample, 1)
    avg_sec_per_block = diff_time / diff_blocks
    if avg_sec_per_block <= 0:
        avg_sec_per_block = 3.0

    blocks_per_day = int(86400 / avg_sec_per_block)
    if blocks_per_day <= 0:
        blocks_per_day = 30000

    return blocks_per_day, latest_block, ts_latest


def get_pair_meta(pair_address: str):
    key = pair_address.lower()
    if key in PAIR_META_CACHE:
        return PAIR_META_CACHE[key]

    contract = w3.eth.contract(
        address=w3.to_checksum_address(pair_address), abi=ABI_PAIR
    )
    token0 = contract.functions.token0().call()
    token1 = contract.functions.token1().call()
    meta = {"token0": token0, "token1": token1}
    PAIR_META_CACHE[key] = meta
    return meta


def _fetch_swap_volume_24h_internal(
    pair_address: str, vee_address: str, debug: bool = False
):
    """
    Liczy wolumen VEE w ostatnich 24h dla danej pary, bez cache.
    Jeśli debug=True, zwraca rozbudowany dict zamiast samej liczby.
    """
    debug_swaps = []
    debug_decode_errors = []
    debug_getlogs_errors = []

    now = int(time.time())

    blocks_per_day, latest_block, ts_latest_block = estimate_blocks_per_day()
    if blocks_per_day == 0:
        if debug:
            return {
                "pair_address": pair_address,
                "vee_address": vee_address,
                "volume_vee": 0.0,
                "now_ts": now,
                "start_block": latest_block,
                "latest_block": latest_block,
                "ts_start_block": ts_latest_block,
                "ts_latest_block": ts_latest_block,
                "swaps": [],
                "decode_errors": debug_decode_errors,
                "getlogs_errors": debug_getlogs_errors,
                "trades": 0,
            }
        return 0.0, 0

    start_block = max(latest_block - blocks_per_day - 2000, 0)
    ts_start_block = get_block_timestamp(start_block)

    meta = get_pair_meta(pair_address)
    vee_lower = vee_address.lower()
    vee_is_token0 = meta["token0"].lower() == vee_lower
    vee_is_token1 = meta["token1"].lower() == vee_lower

    if not (vee_is_token0 or vee_is_token1):
        if debug:
            return {
                "pair_address": pair_address,
                "vee_address": vee_address,
                "volume_vee": 0.0,
                "now_ts": now,
                "start_block": start_block,
                "latest_block": latest_block,
                "ts_start_block": ts_start_block,
                "ts_latest_block": ts_latest_block,
                "swaps": [],
                "decode_errors": debug_decode_errors,
                "getlogs_errors": debug_getlogs_errors,
                "trades": 0,
                "vee_matches_pair": False,
                "token0": meta["token0"],
                "token1": meta["token1"],
            }
        return 0.0, 0

    chunk = 2000
    total_wei = 0
    trades = 0
    pair_checksum = w3.to_checksum_address(pair_address)

    for start in range(start_block, latest_block + 1, chunk):
        end = min(start + chunk - 1, latest_block)
        try:
            # Ronin RPC: Alchemy przyjmuje inty; jeśli znów zobaczymy prefix/format errors, przełączymy na hex.
            logs = w3.eth.get_logs(
                {
                    "fromBlock": int(start),
                    "toBlock": int(end),
                    "address": [w3.to_checksum_address(pair_address)],
                    "topics": [SWAP_TOPIC],
                }
            )
        except Exception as e:
            if debug:
                err_txt = str(e)
                if hasattr(e, "response") and getattr(e, "response") is not None:
                    try:
                        err_txt = f"{err_txt} | body={e.response.text}"
                    except Exception:
                        pass
                debug_getlogs_errors.append(
                    {
                        "fromBlock": int(start),
                        "toBlock": int(end),
                        "error": err_txt,
                    }
                )
            continue

        for log in logs:
            try:
                amount0_in, amount1_in, amount0_out, amount1_out = (
                    w3.codec.decode_abi(
                        ["uint256", "uint256", "uint256", "uint256"],
                        HexBytes(log["data"]),
                    )
                )
            except Exception as e:
                if debug:
                    debug_decode_errors.append(
                        {
                            "blockNumber": log.get("blockNumber"),
                            "txHash": log.get("transactionHash").hex()
                            if log.get("transactionHash")
                            else None,
                            "error": str(e),
                        }
                    )
                continue

            if vee_is_token0:
                vee_delta = amount0_in + amount0_out
            else:
                vee_delta = amount1_in + amount1_out

            if vee_delta > 0:
                total_wei += vee_delta
                trades += 1

                if debug:
                    debug_swaps.append(
                        {
                            "blockNumber": log.get("blockNumber"),
                            "txHash": log.get("transactionHash").hex()
                            if log.get("transactionHash")
                            else None,
                            "amount0_in": str(amount0_in),
                            "amount1_in": str(amount1_in),
                            "amount0_out": str(amount0_out),
                            "amount1_out": str(amount1_out),
                            "vee_delta": str(vee_delta),
                        }
                    )

    volume_vee = total_wei / (10 ** VEE_DECIMALS)

    if debug:
        return {
            "pair_address": pair_address,
            "vee_address": vee_address,
            "volume_vee": volume_vee,
            "now_ts": now,
            "start_block": start_block,
            "latest_block": latest_block,
            "ts_start_block": ts_start_block,
            "ts_latest_block": ts_latest_block,
            "swaps": debug_swaps,
            "decode_errors": debug_decode_errors,
            "getlogs_errors": debug_getlogs_errors,
            "trades": trades,
            "vee_matches_pair": True,
            "token0": meta["token0"],
            "token1": meta["token1"],
        }

    return volume_vee, trades


def fetch_swap_volume_24h(pair_address: str, vee_address: str):
    """
    Publiczna wersja z cache; zwraca (volume, trades).
    """
    now = int(time.time())
    cache_key = pair_address.lower()
    cached = VOLUME_CACHE.get(cache_key)
    if cached and now - cached["ts"] < VOLUME_CACHE_TTL:
        return cached["volume"], cached["trades"]

    volume, trades = _fetch_swap_volume_24h_internal(
        pair_address, vee_address, debug=False
    )
    VOLUME_CACHE[cache_key] = {"ts": now, "volume": volume, "trades": trades}
    return volume, trades


@app.get("/api/market")
def get_latest_snapshots_with_volume():
    rows = query_latest()
    return rows


@app.get("/api/market/{wallet}")
def get_latest_snapshots_with_volume_and_lp(wallet: str):
    data = query_latest()

    # If the address is invalid, return plain data
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
            pair_checksum = w3.to_checksum_address(row["pair_address"])
            contract = w3.eth.contract(address=pair_checksum, abi=ABI_ERC20)

            total = contract.functions.totalSupply().call()
            bal = contract.functions.balanceOf(wallet_checksum).call()

            total_f = total / 1e18 if total > 0 else 0.0
            bal_f = bal / 1e18 if bal > 0 else 0.0

            if total_f <= 0:
                continue

            share = bal_f / total_f

            reserve_item = float(row["reserve_item"] or 0)
            reserve_vee = float(row["reserve_vee"] or 0)

            row["lp_balance"] = bal_f
            row["lp_share"] = share
            row["user_item"] = share * reserve_item
            row["user_vee"] = share * reserve_vee

        except Exception:
            continue

    return data


@app.get("/api/debug/volume/{pair_address}")
def debug_volume(pair_address: str):
    """
    Debug: szczegóły liczenia 24h volume dla konkretnej pary.
    """
    rows = query_latest()
    vee_address = None
    for r in rows:
        if r["pair_address"].lower() == pair_address.lower():
            vee_address = r["vee_address"]
            break

    if vee_address is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Pair not found in snapshots"},
        )

    debug_info = _fetch_swap_volume_24h_internal(
        pair_address, vee_address, debug=True
    )
    return debug_info


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
