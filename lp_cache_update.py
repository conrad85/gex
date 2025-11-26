import os
import psycopg2
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import json
from datetime import datetime

load_dotenv()

DB_PARAMS = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

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
  {
    "constant": true,
    "inputs": [],
    "name": "totalSupply",
    "outputs": [{ "name": "", "type": "uint256" }],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "constant": true,
    "inputs": [{ "name": "owner", "type": "address" }],
    "name": "balanceOf",
    "outputs": [{ "name": "", "type": "uint256" }],
    "stateMutability": "view",
    "type": "function"
  }
]
""")

# Twój LP wallet – możesz też wrzucić do .env jako LP_WALLET
WALLET = os.getenv("LP_WALLET", "0x2aEb84d9b061C850B1F3C8C5200BaE14270D49f0")


def fetch_pairs():
    """
    Bierzemy ostatnie snapshoty każdej pary z gex_snapshots,
    żeby znać aktualne rezerwy VEE / item.
    """
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute("""
        SELECT pair_address, item_name, reserve_vee, reserve_item
        FROM gex_snapshots s
        WHERE ts = (
            SELECT MAX(ts)
            FROM gex_snapshots s2
            WHERE s2.pair_address = s.pair_address
        )
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def update_cache():
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    wallet = Web3.to_checksum_address(WALLET)

    pairs = fetch_pairs()
    print(f"Found {len(pairs)} pairs to refresh in LP cache.")

    for (pair_address, item_name, reserve_vee, reserve_item) in pairs:
        try:
            pair_checksum = Web3.to_checksum_address(pair_address)
            contract = w3.eth.contract(address=pair_checksum, abi=ABI_ERC20)

            total = contract.functions.totalSupply().call()
            bal = contract.functions.balanceOf(wallet).call()

            total_f = total / 1e18 if total else 0.0
            bal_f  = bal / 1e18 if bal else 0.0
            share  = bal_f / total_f if total_f > 0 else 0.0

            user_vee  = share * float(reserve_vee or 0)
            user_item = share * float(reserve_item or 0)

            cur.execute("""
                INSERT INTO lp_cache (pair_address, item_name, ts, lp_balance, lp_share, user_vee, user_item)
                VALUES (%s, %s, NOW(), %s, %s, %s, %s)
                ON CONFLICT (pair_address)
                DO UPDATE SET
                    ts = NOW(),
                    item_name = EXCLUDED.item_name,
                    lp_balance = EXCLUDED.lp_balance,
                    lp_share = EXCLUDED.lp_share,
                    user_vee = EXCLUDED.user_vee,
                    user_item = EXCLUDED.user_item;
            """, (pair_address, item_name, bal_f, share, user_vee, user_item))

        except Exception as e:
            print(f"[ERROR] pair {pair_address}: {e}")

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    print(f"[{datetime.utcnow()}] Updating LP cache...")
    update_cache()
    print(f"[{datetime.utcnow()}] LP cache updated.")
