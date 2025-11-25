import os
import json
import time
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware  # <– DODAJ TEN IMPORT

load_dotenv()

# === KONFIGURACJA BAZY DANYCH ===
DB_PARAMS = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "gex"),
    "user": os.getenv("DB_USER", "gex_user"),
    "password": os.getenv("DB_PASS", "gex_pass"),
}

# === KONFIGURACJA BLOCKCHAIN (ZAWSZE HTTP) ===
RPC_DEFAULT = "https://ronin-mainnet.g.alchemy.com/v2/IJPvvQ6YdcbcF85OD8jNsjBrpGo3-Xh0"
RPC_RAW = os.getenv("RONIN_RPC", RPC_DEFAULT)

# jeśli wpiszesz kiedyś w .env wss://..., to tu się zamieni na https://
if RPC_RAW.startswith("wss://"):
    RPC_HTTP = "https://" + RPC_RAW.removeprefix("wss://")
else:
    RPC_HTTP = RPC_RAW

w3 = Web3(Web3.HTTPProvider(RPC_HTTP))

# Ronin = POA, więc dokładnie jak w server.py
try:
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
except Exception:
    # jak już wstrzyknięte / nie trzeba – olewamy
    pass

# === ABI: UNISWAP V2 PAIR ===
ABI_PAIR = json.loads("""
[
  {
    "constant": true,
    "inputs": [],
    "name": "getReserves",
    "outputs": [
      {"internalType":"uint112","name":"_reserve0","type":"uint112"},
      {"internalType":"uint112","name":"_reserve1","type":"uint112"},
      {"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}
    ],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "constant": true,
    "inputs": [],
    "name": "token0",
    "outputs": [{"internalType":"address","name":"","type":"address"}],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "constant": true,
    "inputs": [],
    "name": "token1",
    "outputs": [{"internalType":"address","name":"","type":"address"}],
    "stateMutability": "view",
    "type": "function"
  }
]
""")

# === FUNKCJE POMOCNICZE ===

def connect_db():
    return psycopg2.connect(**DB_PARAMS)


def get_active_pairs():
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT pair_address, item_name, item_address, vee_address
            FROM gex_pairs
            WHERE enabled = TRUE;
        """)
        return cur.fetchall()


def to_checksum_all(w3, pair_address, item_address, vee_address):
    pa = w3.to_checksum_address(pair_address)
    ia = w3.to_checksum_address(item_address) if item_address else None
    va = w3.to_checksum_address(vee_address) if vee_address else None
    return pa, ia, va


def get_reserves_for_pair(w3, pair_address, item_name, item_address, vee_address):
    """
    Pobiera rezerwy i oblicza cenę z 5% markupiem (jak w GEX UI).
    """
    pair_address, item_address, vee_address = to_checksum_all(
        w3, pair_address, item_address, vee_address
    )

    code = w3.eth.get_code(pair_address)
    if code in (b"", b"\x00"):
        raise RuntimeError(f"{pair_address} nie ma bytecode — to nie jest LP.")

    pair = w3.eth.contract(address=pair_address, abi=ABI_PAIR)

    token0 = pair.functions.token0().call()
    token1 = pair.functions.token1().call()

    r0, r1, _ = pair.functions.getReserves().call()
    reserve0 = r0 / 1e18
    reserve1 = r1 / 1e18

    if token0.lower() == vee_address.lower():
        reserve_vee = reserve0
        reserve_item = reserve1
    elif token1.lower() == vee_address.lower():
        reserve_vee = reserve1
        reserve_item = reserve0
    else:
        raise RuntimeError(f"LP {pair_address} nie zawiera VEE.")

    # === CENY: surowa + 5% markup (jak w GEX UI) ===
    raw_price = reserve_vee / reserve_item if reserve_item > 0 else 0.0
    price_vee = raw_price * 1.05  # 5% fee z GEX

    return {
        "pair_address": pair_address,
        "item_name": item_name,
        "price_vee": price_vee,
        "reserve_vee": reserve_vee,
        "reserve_item": reserve_item,
        "vee_address": vee_address,
        "item_address": item_address
    }


def insert_snapshots(rows):
    if not rows:
        return

    ts = datetime.now(timezone.utc)
    values = [
        (
            ts,
            r["pair_address"],
            r["item_name"],
            r["price_vee"],
            r["reserve_vee"],
            r["reserve_item"],
            r["vee_address"],
            r["item_address"],
        )
        for r in rows
    ]

    sql = """
        INSERT INTO gex_snapshots (
            ts, pair_address, item_name,
            price_vee,
            reserve_vee, reserve_item,
            vee_address, item_address
        ) VALUES %s
        ON CONFLICT (pair_address, ts) DO NOTHING;
    """

    with connect_db() as conn, conn.cursor() as cur:
        execute_values(cur, sql, values)
        conn.commit()

    for r in rows:
        raw = r['reserve_vee'] / r['reserve_item'] if r['reserve_item'] > 0 else 0
        print(f"[{ts}] {r['item_name']}: {r['price_vee']:.6f} VEE (surowa: {raw:.6f})")


def main():
    # używamy globalnego w3 na HTTP z wstrzykniętym POA middleware
    if not w3.is_connected():
        print("Brak połączenia z Ronin RPC!")
        return

    pairs = get_active_pairs()
    if not pairs:
        print("Brak aktywnych par w gex_pairs.")
        return

    snapshots = []
    for pair_address, item_name, item_address, vee_address in pairs:
        try:
            data = get_reserves_for_pair(w3, pair_address, item_name, item_address, vee_address)
            snapshots.append(data)
        except Exception as e:
            print(f"Błąd przy {item_name} [{pair_address}]: {e}")
        time.sleep(0.15)

    insert_snapshots(snapshots)


if __name__ == "__main__":
    main()
