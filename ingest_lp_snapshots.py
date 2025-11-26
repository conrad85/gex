#!/usr/bin/env python3
import os
import json

import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv()

DB_PARAMS = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

WALLET = os.getenv(
    "LP_WALLET",
    "0x2aEb84d9b061C850B1F3C8C5200BaE14270D49f0"
).lower()

RPC_DEFAULT = "https://ronin-mainnet.g.alchemy.com/v2/IJPvvQ6YdcbcF85OD8jNsjBrpGo3-Xh0"
RPC_RAW = os.getenv("RONIN_RPC", RPC_DEFAULT)
if RPC_RAW.startswith("wss://"):
    RPC_HTTP = "https://" + RPC_RAW.removeprefix("wss://")
else:
    RPC_HTTP = RPC_RAW

w3 = Web3(Web3.HTTPProvider(RPC_HTTP))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

ABI_ERC20 = json.loads("""
[
  { "constant": true, "inputs": [], "name": "totalSupply",
    "outputs": [{ "name": "", "type": "uint256" }],
    "stateMutability": "view", "type": "function"
  },
  { "constant": true, "inputs": [{ "name": "owner", "type": "address" }],
    "name": "balanceOf",
    "outputs": [{ "name": "", "type": "uint256" }],
    "stateMutability": "view", "type": "function"
  }
]
""")

FEE_RATE = float(os.getenv("LP_FEE_RATE", "0.05"))      # 5% fee model
LP_MIN_SHARE = float(os.getenv("LP_MIN_SHARE", "0.0001"))  # ignoruj resztki LP


def get_conn():
    return psycopg2.connect(**DB_PARAMS)


def query_latest(conn):
    cur = conn.cursor()
    cur.execute(
        """
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
                COALESCE(SUM(vee_amount), 0) AS volume_24h_vee
            FROM trades_ronin
            WHERE ts >= NOW() - INTERVAL '24 hours'
            GROUP BY LOWER(pair_address)
        ),
        vol7 AS (
            SELECT
                LOWER(pair_address) AS pair_lower,
                COALESCE(SUM(vee_amount), 0) AS volume_7d_vee
            FROM trades_ronin
            WHERE ts >= NOW() - INTERVAL '7 days'
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
            COALESCE(v24.volume_24h_vee, 0) AS volume_24h_vee,
            COALESCE(v7.volume_7d_vee, 0)   AS volume_7d_vee
        FROM latest l
        LEFT JOIN vol24 v24 ON v24.pair_lower = l.pair_lower
        LEFT JOIN vol7  v7  ON v7.pair_lower  = l.pair_lower;
        """
    )
    rows = cur.fetchall()
    cur.close()
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
        "volume_7d_vee",
    ]
    return [dict(zip(columns, r)) for r in rows]


def get_lp_info(pair_address: str, wallet_checksum: str):
    contract = w3.eth.contract(
        address=w3.to_checksum_address(pair_address), abi=ABI_ERC20
    )
    total = contract.functions.totalSupply().call()
    bal = contract.functions.balanceOf(wallet_checksum).call()

    if total == 0:
        return 0.0, 0.0

    total_f = total / 1e18
    bal_f = bal / 1e18
    share = bal_f / total_f if total_f > 0 else 0.0
    return bal_f, share


def main():
    conn = get_conn()

    # prosty mutex na bazie
    cur = conn.cursor()
    cur.execute("SELECT pg_try_advisory_lock(987654322)")
    got_lock = cur.fetchone()[0]
    if not got_lock:
        print("[LP] Inna instancja ingest_lp_snapshots już działa – wychodzę.")
        cur.close()
        conn.close()
        return
    cur.close()

    wallet_checksum = w3.to_checksum_address(WALLET)
    latest = query_latest(conn)

    rows_to_insert = []

    for row in latest:
        pair = row["pair_address"]
        try:
            lp_balance, lp_share = get_lp_info(pair, wallet_checksum)
        except Exception as e:
            print(f"[LP] Błąd przy odczycie LP dla {pair}: {e}")
            continue

        if lp_share < LP_MIN_SHARE:
            continue  # praktycznie brak pozycji

        reserve_vee = float(row["reserve_vee"] or 0)
        reserve_item = float(row["reserve_item"] or 0)
        price_vee = float(row["price_vee"] or 0)
        vol24 = float(row["volume_24h_vee"] or 0)
        vol7 = float(row["volume_7d_vee"] or 0)

        user_vee = lp_share * reserve_vee
        user_item = lp_share * reserve_item

        lp_earn_24h = vol24 * FEE_RATE * lp_share
        lp_earn_7d = vol7 * FEE_RATE * lp_share

        lp_value = user_vee + user_item * price_vee
        lp_apr = None
        if lp_value > 0 and lp_earn_7d > 0:
            daily = lp_earn_7d / 7.0
            lp_apr = (daily * 365.0 / lp_value) * 100.0

        rows_to_insert.append(
            (
                WALLET,
                pair,
                row["item_name"],
                price_vee,
                reserve_vee,
                reserve_item,
                lp_balance,
                lp_share,
                user_vee,
                user_item,
                vol24,
                vol7,
                lp_earn_24h,
                lp_earn_7d,
                lp_apr,
            )
        )

    if rows_to_insert:
        cur = conn.cursor()
        execute_batch(
            cur,
            """
            INSERT INTO lp_snapshots (
                wallet_address,
                pair_address,
                item_name,
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
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            rows_to_insert,
        )
        conn.commit()
        cur.close()
        print(f"[LP] Zapisano {len(rows_to_insert)} snapshotów LP.")

    # unlock
    cur = conn.cursor()
    cur.execute("SELECT pg_advisory_unlock(987654322)")
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
