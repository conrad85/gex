from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse

import os
import time
import traceback
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

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


# ================== KONFIG ==================

DB_PARAMS = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
}

# Fee % od wolumenu, które trafia do LP (np. 0.05 = 5%)
LP_FEE_RATE = float(os.getenv("LP_FEE_RATE", "0.05"))

# Domyślna cena VEE w USD, gdyby w DB nic nie było
VEE_USD_FALLBACK = float(os.getenv("VEE_USD", "0") or "0")

# Cache ceny VEE (żeby nie mielić DB co request)
VEE_PRICE_CACHE = {
    "ts": 0.0,
    "price": VEE_USD_FALLBACK,
}

# Minimalna liczba dni pozycji, żeby liczyć IL annualized
MIN_DAYS_FOR_IL_ANNUALIZED = float(os.getenv("MIN_DAYS_IL_ANNUALIZED", "3.0"))


def get_vee_usd_price() -> float:
    """
    Cena VEE w USD z tabeli vee_price_snapshots, z prostym cachem.
    TTL cache: 240s. Jak coś pójdzie nie tak, trzymamy ostatnią znaną wartość.
    """
    now = time.time()
    if now - VEE_PRICE_CACHE["ts"] < 240 and VEE_PRICE_CACHE["price"] > 0:
        return VEE_PRICE_CACHE["price"]

    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        cur.execute(
            "SELECT price_usd FROM vee_price_snapshots ORDER BY ts DESC LIMIT 1;"
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row and row[0] is not None:
            VEE_PRICE_CACHE["price"] = float(row[0])
            VEE_PRICE_CACHE["ts"] = now
    except Exception as e:
        # Jak padnie, trudno – zostaje to, co było w cache / fallback
        print("get_vee_usd_price ERROR:", repr(e))

    return VEE_PRICE_CACHE["price"]


# ================== MARKET SNAPSHOTS ==================


def query_latest():
    """
    Ostatni snapshot każdej pary z gex_snapshots
    + wolumen 24h / 7d z trades_ronin
    + zmiany ceny i wolumenu.
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
        CASE
            WHEN p24.price_24h_ago IS NULL OR p24.price_24h_ago = 0 THEN NULL
            ELSE ((l.price_vee - p24.price_24h_ago) / p24.price_24h_ago) * 100
        END AS price_change_24h_pct,
        CASE
            WHEN p7.price_7d_ago IS NULL OR p7.price_7d_ago = 0 THEN NULL
            ELSE ((l.price_vee - p7.price_7d_ago) / p7.price_7d_ago) * 100
        END AS price_change_7d_pct,
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

    out = []
    for row in rows:
        d = dict(zip(columns, row))
        # konwersja do float gdzie potrzeba
        for k in [
            "price_vee",
            "reserve_vee",
            "reserve_item",
            "volume_24h_vee",
            "volume_24h_trades",
            "volume_7d_vee",
            "volume_7d_trades",
            "price_24h_ago",
            "price_7d_ago",
            "volume_24h_prev_vee",
            "volume_7d_prev_vee",
            "volume_change_24h_pct",
            "volume_change_7d_pct",
        ]:
            if d.get(k) is not None:
                d[k] = float(d[k])
        if isinstance(d.get("ts"), datetime):
            d["ts"] = d["ts"].isoformat()
        out.append(d)

    return out


# ================== LP SNAPSHOTS ==================


def query_lp_latest(wallet: str):
    """
    Ostatni snapshot z lp_snapshots dla każdej pary.
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
            if d.get(k) is not None:
                d[k] = float(d[k])
        if isinstance(d.get("ts"), datetime):
            d["ts"] = d["ts"].isoformat()
        result.append(d)

    return result


def query_lp_history(wallet: str):
    """
    Pełna historia LP dla walleta (pod liczenie IL).
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
            if d.get(k) is not None:
                d[k] = float(d[k])
        # ts zostaje datetime
        out.append(d)

    return out


def calc_il(entry_vee, entry_item, cur_vee, cur_item, price_vee):
    """
    IL w VEE:
    value_hodl = entry_vee + entry_item * price_now
    value_lp   = cur_vee   + cur_item   * price_now
    il_vee     = value_lp - value_hodl
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
    IL per para + prosty scoring "net_effective_pct"
    (lp_apr + IL annualized, jeśli ma sens).
    """
    history = query_lp_history(wallet)
    if not history:
        return []

    per_pair = {}
    for row in history:
        key = row["pair_address"].lower()
        per_pair.setdefault(key, []).append(row)

    results = []

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

        # ile dni w pozycji
        try:
            t0 = entry["ts"]
            t1 = current["ts"]
            delta_days = max((t1 - t0).total_seconds() / 86400.0, 0.0)
        except Exception:
            delta_days = 0.0

        # annualizacja tylko jeśli pozycja jest starsza niż X dni
        if (
            delta_days <= 0
            or il_pct is None
            or delta_days < MIN_DAYS_FOR_IL_ANNUALIZED
        ):
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

        vee_usd = get_vee_usd_price()
        il_usd = None
        value_hodl_usd = None
        value_lp_usd = None
        if vee_usd and vee_usd > 0:
            il_usd = il_vee * vee_usd
            value_hodl_usd = value_hodl * vee_usd
            value_lp_usd = value_lp * vee_usd

        results.append(
            {
                "pair_address": current["pair_address"],
                "item_name": current.get("item_name"),
                "entry_ts": entry["ts"].isoformat()
                if isinstance(entry["ts"], datetime)
                else entry["ts"],
                "current_ts": current["ts"].isoformat()
                if isinstance(current["ts"], datetime)
                else current["ts"],
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
    total_score = sum(r["net_effective_pct"] for r in positive) if positive else 0.0

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
    """
    Lista wszystkich par z ceną + volume (bez LP).
    """
    return query_latest()


@app.get("/api/market/{wallet}")
def get_latest_snapshots_with_volume_and_lp(wallet: str):
    """
    Market + LP dla portfela.
    LP bierzemy z tabeli lp_cache (single wallet), wallet w URL
    jest tu tylko po to, żeby front miał ładne /api/market/{wallet}.
    """
    data = query_latest()

    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            LOWER(pair_address) AS pair_lower,
            lp_balance,
            lp_share,
            user_vee,
            user_item
        FROM lp_cache
        """
    )
    lp_rows = cur.fetchall()
    cur.close()
    conn.close()

    lp_by_pair = {}
    for pair_lower, lp_balance, lp_share, user_vee, user_item in lp_rows:
        lp_by_pair[pair_lower] = {
            "lp_balance": float(lp_balance or 0),
            "lp_share": float(lp_share or 0),
            "user_vee": float(user_vee or 0),
            "user_item": float(user_item or 0),
        }

    for row in data:
        pair_lower = row["pair_address"].lower()

        row["lp_balance"] = 0.0
        row["lp_share"] = 0.0
        row["user_item"] = 0.0
        row["user_vee"] = 0.0
        row["lp_earn_vee_24h"] = 0.0
        row["lp_earn_vee_7d"] = 0.0

        lp_info = lp_by_pair.get(pair_lower)
        if not lp_info:
            continue

        lp_share = lp_info["lp_share"]

        row["lp_balance"] = lp_info["lp_balance"]
        row["lp_share"] = lp_share
        row["user_item"] = lp_info["user_item"]
        row["user_vee"] = lp_info["user_vee"]

        vol24 = float(row.get("volume_24h_vee") or 0.0)
        vol7 = float(row.get("volume_7d_vee") or 0.0)

        if lp_share > 0 and LP_FEE_RATE > 0:
            row["lp_earn_vee_24h"] = vol24 * LP_FEE_RATE * lp_share
            row["lp_earn_vee_7d"] = vol7 * LP_FEE_RATE * lp_share

    return data


@app.get("/api/history/{pair_address}")
def get_pair_history(pair_address: str):
    """
    Historia ceny, rezerw i dziennego wolumenu dla pary.
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
def api_get_lp_latest(wallet: str):
    return query_lp_latest(wallet)


@app.get("/api/lp/{wallet}/il")
def api_get_lp_il(wallet: str):
    results = compute_lp_il_for_wallet(wallet)
    vee_usd = get_vee_usd_price()
    return {
        "wallet": wallet,
        "vee_usd_price": vee_usd,
        "pairs": results,
    }


@app.get("/api/vee_price")
def api_get_vee_price():
    price = get_vee_usd_price()
    return {"vee_usd": price}


@app.get("/api/mm/log", response_class=PlainTextResponse)
def get_mm_log():
    try:
        with open("mm_bot.log", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Log file not found."
    
@app.get("/api/mm/market")
def mm_market_scan():
    from mm_market import fetch_market_data
    return {"pairs": fetch_market_data()}



if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
