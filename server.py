from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import os
from dotenv import load_dotenv
from web3 import Web3
import json

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PARAMS = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

# === RPC: zawsze HTTP dla serwera (bez jazd z websocketem) ===
RPC_RAW = os.getenv("RONIN_RPC", "https://ronin.drpc.org")
if RPC_RAW.startswith("wss://"):
    RPC_HTTP = "https://" + RPC_RAW.removeprefix("wss://")
else:
    RPC_HTTP = RPC_RAW

w3 = Web3(Web3.HTTPProvider(RPC_HTTP))

ABI_ERC20 = json.loads("""
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
""")


def query_latest_with_volume():
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    query = """
    WITH latest AS (
        SELECT DISTINCT ON (pair_address)
            pair_address,
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
    diffs AS (
        SELECT
            pair_address,
            ABS(reserve_item - LAG(reserve_item) OVER (
                PARTITION BY pair_address ORDER BY ts
            )) AS delta_item
        FROM gex_snapshots
        WHERE ts >= NOW() - INTERVAL '24 hours'
    ),
    vol AS (
        SELECT
            pair_address,
            COALESCE(SUM(delta_item), 0) AS volume_24h_est
        FROM diffs
        GROUP BY pair_address
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
        COALESCE(v.volume_24h_est, 0) AS volume_24h_est
    FROM latest l
    LEFT JOIN vol v USING (pair_address);
    """

    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    columns = [
        "pair_address", "item_name", "price_vee",
        "reserve_vee", "reserve_item", "vee_address",
        "item_address", "ts", "volume_24h_est"
    ]

    return [dict(zip(columns, row)) for row in rows]


@app.get("/api/market")
def get_latest_snapshots_with_volume():
    # Wersja bez LP info (jak wcześniej)
    return query_latest_with_volume()


@app.get("/api/market/{wallet}")
def get_latest_snapshots_with_volume_and_lp(wallet: str):
    data = query_latest_with_volume()

    # Jeśli w ogóle nie ogarnia adresu, zwracamy gołe dane
    try:
        wallet_checksum = w3.to_checksum_address(wallet)
    except Exception:
        return data

    for row in data:
        # Domyślne wartości, jak coś się wysypie przy RPC
        row["lp_balance"] = 0.0
        row["lp_share"] = 0.0
        row["user_item"] = 0.0
        row["user_vee"] = 0.0

        try:
            pair_checksum = w3.to_checksum_address(row["pair_address"])
            contract = w3.eth.contract(address=pair_checksum, abi=ABI_ERC20)

            total = contract.functions.totalSupply().call()
            bal = contract.functions.balanceOf(wallet_checksum).call()

            # zakładamy 18 decimali w LP
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
            # jak kontrakt się wywali (para martwa / cokolwiek) -> zostają zera
            continue

    return data


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
