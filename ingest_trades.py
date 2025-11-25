import os
import time
import json
import traceback
from decimal import Decimal

import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from hexbytes import HexBytes

# ================== CONFIG / INIT ==================

load_dotenv()

DB_PARAMS = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

# RPC: używamy tego samego co w server.py, default Alchemy Ronin
RPC_DEFAULT = "https://ronin-mainnet.g.alchemy.com/v2/IJPvvQ6YdcbcF85OD8jNsjBrpGo3-Xh0"
RPC_RAW = os.getenv("RONIN_RPC", RPC_DEFAULT)
if RPC_RAW.startswith("wss://"):
    RPC_HTTP = "https://" + RPC_RAW.removeprefix("wss://")
else:
    RPC_HTTP = RPC_RAW

w3 = Web3(Web3.HTTPProvider(RPC_HTTP))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

TRADES_START_BLOCK_ENV = os.getenv("TRADES_START_BLOCK", "").strip()
BLOCK_STEP = 10           # bo Alchemy na free tierze ma limit 10 bloków dla eth_getLogs
VEE_DECIMALS = 18

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

SWAP_TOPIC = Web3.to_hex(
    Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)")
)

PAIR_META_CACHE = {}


# ================== DB HELPERS ==================

def get_conn():
    return psycopg2.connect(**DB_PARAMS)


def ensure_tables(conn):
    cur = conn.cursor()

    # trades_ronin
    cur.execute("SELECT to_regclass('public.trades_ronin')")
    trades_exists = cur.fetchone()[0] is not None
    if not trades_exists:
        try:
            cur.execute(
                """
                CREATE TABLE trades_ronin (
                    id           bigserial PRIMARY KEY,
                    pair_address text        NOT NULL,
                    vee_address  text        NOT NULL,
                    block_number bigint      NOT NULL,
                    tx_hash      text        NOT NULL,
                    log_index    integer     NOT NULL,
                    ts           timestamptz NOT NULL,
                    vee_amount   numeric(38,18) NOT NULL
                );
                """
            )
        except Exception as e:
            print(f"[INGEST] WARNING: cannot create trades_ronin ({e}), assuming it exists")

    # indexes (best-effort; may fail if we are not the owner)
    try:
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS trades_ronin_unique
            ON trades_ronin (pair_address, tx_hash, log_index);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS trades_ronin_pair_ts_idx
            ON trades_ronin (pair_address, ts);
            """
        )
    except Exception as e:
        print(f"[INGEST] WARNING: cannot create indexes on trades_ronin ({e})")
        conn.rollback()

    # trades_cursor
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades_cursor (
                id         integer PRIMARY KEY,
                last_block bigint NOT NULL
            );
            """
        )
    except Exception as e:
        print(f"[INGEST] WARNING: cannot create trades_cursor ({e})")
        conn.rollback()

    conn.commit()
    cur.close()


def get_pairs(conn):
    """
    Bierzemy wszystkie pary, które znasz z gex_snapshots
    (pair_address + vee_address). Duplikaty kasujemy w pythonie.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT pair_address, vee_address
        FROM gex_snapshots
        """
    )
    rows = cur.fetchall()
    cur.close()

    pairs = []
    for pair_address, vee_address in rows:
        if not pair_address or not vee_address:
            continue
        pairs.append(
            (
                pair_address.lower(),
                vee_address.lower(),
            )
        )
    # usuwamy duplikaty
    pairs = list({(p, v) for p, v in pairs})
    return pairs


def get_last_block(conn):
    cur = conn.cursor()
    cur.execute("SELECT last_block FROM trades_cursor WHERE id = 1")
    row = cur.fetchone()
    cur.close()

    if row:
        return int(row[0])

    # pierwszy raz: bierzemy z env, albo latest-5000
    latest = w3.eth.block_number
    if TRADES_START_BLOCK_ENV:
        try:
            start = int(TRADES_START_BLOCK_ENV)
        except ValueError:
            start = max(latest - 5000, 0)
    else:
        start = max(latest - 5000, 0)

    print(f"[INGEST] Pierwszy raz, startuję od bloku {start}")
    return start


def save_last_block(conn, last_block):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO trades_cursor (id, last_block)
        VALUES (1, %s)
        ON CONFLICT (id) DO UPDATE SET last_block = EXCLUDED.last_block
        """,
        (int(last_block),),
    )
    conn.commit()
    cur.close()


# ================== WEB3 HELPERS ==================

def get_pair_meta(pair_address: str):
    key = pair_address.lower()
    if key in PAIR_META_CACHE:
        return PAIR_META_CACHE[key]

    contract = w3.eth.contract(
        address=w3.to_checksum_address(pair_address),
        abi=ABI_PAIR,
    )
    token0 = contract.functions.token0().call()
    token1 = contract.functions.token1().call()
    meta = {
        "token0": token0.lower(),
        "token1": token1.lower(),
    }
    PAIR_META_CACHE[key] = meta
    return meta


def get_block_timestamp(block_number: int) -> int:
    block = w3.eth.get_block(block_number)
    return int(block["timestamp"])


# ================== CORE INGEST ==================

def decode_swap(log, vee_is_token0: bool):
    """
    Z dekodowanego eventu Swap wylicza ilość VEE.
    """
    amount0_in, amount1_in, amount0_out, amount1_out = w3.codec.decode(
        ["uint256", "uint256", "uint256", "uint256"],
        HexBytes(log["data"]),
    )

    if vee_is_token0:
        vee_delta = amount0_in + amount0_out
    else:
        vee_delta = amount1_in + amount1_out

    if vee_delta <= 0:
        return None

    vee_amount = Decimal(vee_delta) / (Decimal(10) ** VEE_DECIMALS)
    return vee_amount, amount0_in, amount1_in, amount0_out, amount1_out


def ingest():
    conn = get_conn()
    ensure_tables(conn)

    pairs = get_pairs(conn)
    if not pairs:
        print("[INGEST] Brak par w gex_snapshots – nie mam czego śledzić.")
        return

    print(f"[INGEST] Pary do śledzenia: {len(pairs)}")
    for p, v in pairs:
        print(f"    {p}  (VEE: {v})")

    # mapowanie para -> vee, żeby szybko ogarnąć w pętli
    pair_to_vee = {p: v for p, v in pairs}
    pair_addresses_checksum = [
        w3.to_checksum_address(p) for p, _ in pairs
    ]

    latest_block = w3.eth.block_number
    start_block = get_last_block(conn)
    if start_block >= latest_block:
        print(
            f"[INGEST] Nic do zrobienia (start_block={start_block}, latest={latest_block})"
        )
        return

    print(
        f"[INGEST] Skanuję od bloku {start_block + 1} do {latest_block} (krok {BLOCK_STEP})"
    )

    total_inserted = 0
    total_logs = 0

    cur = conn.cursor()

    # we may not own the sequence, so manage IDs manually
    try:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM trades_ronin")
        next_id = int(cur.fetchone()[0]) + 1
    except Exception as e:
        print(f"[INGEST] WARNING: cannot read max(id) from trades_ronin ({e}), fallback to 1")
        next_id = 1

    current_from = start_block + 1
    while current_from <= latest_block:
        current_to = min(current_from + BLOCK_STEP - 1, latest_block)

        try:
            logs = w3.eth.get_logs(
                {
                    "fromBlock": hex(int(current_from)),
                    "toBlock": hex(int(current_to)),
                    "address": pair_addresses_checksum,
                    "topics": [SWAP_TOPIC],
                }
            )
        except Exception as e:
            # Alchemy lubi rzucać jasne komunikaty, więc je wypisujemy i lecimy dalej
            print(
                f"[INGEST] get_logs failed for {current_from}-{current_to}: {e}"
            )
            current_from = current_to + 1
            continue

        if logs:
            print(
                f"[INGEST] Bloki {current_from}-{current_to}: {len(logs)} logów"
            )
        total_logs += len(logs)

        rows_to_insert = []
        for log in logs:
            try:
                pair_addr = log["address"].lower()
                vee_addr = pair_to_vee.get(pair_addr)
                if not vee_addr:
                    # para spoza listy – ignorujemy
                    continue

                meta = get_pair_meta(pair_addr)
                vee_is_token0 = meta["token0"] == vee_addr
                vee_is_token1 = meta["token1"] == vee_addr
                if not (vee_is_token0 or vee_is_token1):
                    # coś bardzo nie tak, ale nie zabijamy ingestu
                    continue

                vee_amount_info = decode_swap(log, vee_is_token0=vee_is_token0)
                if vee_amount_info is None:
                    continue
                vee_amount, a0in, a1in, a0out, a1out = vee_amount_info

                block_number = int(log["blockNumber"])
                ts = get_block_timestamp(block_number)

                tx_hash = log["transactionHash"].hex()
                log_index = int(log["logIndex"])

                rows_to_insert.append(
                    (
                        next_id,
                        pair_addr,
                        vee_addr,
                        block_number,
                        tx_hash,
                        log_index,
                        time.strftime(
                            "%Y-%m-%d %H:%M:%S%z",
                            time.localtime(ts),
                        ),
                        str(vee_amount),
                    )
                )
                next_id += 1
            except Exception as e:
                print(
                    f"[INGEST] ERROR parsing log in {current_from}-{current_to}: {e}"
                )
                traceback.print_exc()
                continue

        if rows_to_insert:
            try:
                execute_batch(
                    cur,
                    """
                    INSERT INTO trades_ronin (
                        id,
                        pair_address,
                        vee_address,
                        block_number,
                        tx_hash,
                        log_index,
                        ts,
                        vee_amount
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (pair_address, tx_hash, log_index) DO NOTHING
                    """,
                    rows_to_insert,
                )
                conn.commit()
                total_inserted += cur.rowcount
            except Exception as e:
                print(f"[INGEST] ERROR during insert batch: {e}")
                conn.rollback()

        current_from = current_to + 1

    save_last_block(conn, latest_block)
    cur.close()
    conn.close()

    print(
        f"[INGEST] Zakończone. Znalazłem {total_logs} logów, wstawione nowe wiersze: ~{total_inserted}"
    )


if __name__ == "__main__":
    try:
        ingest()
    except KeyboardInterrupt:
        print("[INGEST] Przerwane przez użytkownika.")
    except Exception as e:
        print(f"[INGEST] Fatal error: {e}")
        traceback.print_exc()
