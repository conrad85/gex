GEX – Zeeverse Market Monitor (Ronin)

Monitor rynku Zeeverse GEX (on-chain Ronin):

- snapshoty LP par (rezerwy itemów + VEE),
- ingest swapów on-chain z Ronin (24h/7d wolumen),
- wyliczenia price/volume change 24h/7d,
- LP udział usera, LP APR, LP fees,
- REST API (FastAPI),
- frontend HTML/JS + item detail page,
- snapshoty LP do osobnej tabeli (lp_snapshots),
- pełna konfiguracja produkcyjna (nginx reverse proxy + systemd).

🟢 Ten README odzwierciedla OBECNĄ konfigurację działającą na VPS.

---

## 📁 Struktura projektu

`/root/gex`

```text
/root/gex
│
├── server.py               # API FastAPI
├── ingest_pairs.py         # snapshot LP → gex_snapshots
├── ingest_trades.py        # swap ingest → trades_ronin
├── ingest_lp_snapshots.py  # zapis LP usera do lp_snapshots
│
├── gex_pairs_seed.sql
├── trades_schema.sql
│
├── frontend/               # źródło prawdy dla frontendu (git)
│   ├── index.html
│   ├── app.js
│   ├── item.html
│   ├── item.js
│   └── styles.css
│
└── requirements.txt
📍 Produkcyjny katalog frontendu (nginx):

Źródło: /root/gex/frontend

Serwowany katalog: /var/www/gex-frontend (nie pod gitem, nadpisywany przy deployu)

⚙️ Wymagania
Python 3.10+

PostgreSQL 14+ (produkcyjnie 14.19)

RPC Ronin (Alchemy / inne HTTP)

Brak Node – czysty frontend (HTML/JS/CSS)

🌱 Konfiguracja .env
/root/gex/.env:

env
Skopiuj kod
DB_HOST=localhost
DB_PORT=5432
DB_NAME=gex
DB_USER=gex_user
DB_PASS=********

RONIN_RPC=https://ronin-mainnet.g.alchemy.com/v2/<API_KEY>

# opcjonalne przy full resync:
# TRADES_START_BLOCK=50000000

# szacowany fee rate, który trafia do LP (np. 0.05 = 5%)
LP_FEE_RATE=0.05
🗄️ Schemy bazodanowe
gex_snapshots – snapshot rezerw LP
Tworzone przez ingest_pairs.py:

ts – timestamp snapshotu,

pair_address,

item_name,

price_vee,

reserve_vee,

reserve_item,

vee_address,

item_address.

Zalecany index/unique:

sql
Skopiuj kod
CREATE UNIQUE INDEX IF NOT EXISTS gex_snapshots_pair_ts_uniq
ON gex_snapshots (pair_address, ts);
trades_ronin – swap eventy
Tworzone przez trades_schema.sql lub automatycznie przez ingest_trades.py (best-effort).

Kolumny:

id bigserial PRIMARY KEY,

pair_address,

vee_address,

block_number,

tx_hash,

log_index,

ts,

vee_amount numeric(38,18)

🔎 Definicja wolumenu:

Wolumen w vee_amount liczony jest jako:

(VEE in + VEE out) / 2 / 1e18

czyli standardowo, bez podwajania volume.

Zalecane indexy:

sql
Skopiuj kod
CREATE UNIQUE INDEX IF NOT EXISTS trades_ronin_unique
ON trades_ronin (pair_address, tx_hash, log_index);

CREATE INDEX IF NOT EXISTS trades_ronin_pair_ts_idx
ON trades_ronin (pair_address, ts);
lp_snapshots – snapshot użytkownika (LP/fees/APR)
Tworzone ręcznie (już istnieje na VPS):

sql
Skopiuj kod
CREATE TABLE lp_snapshots (
    id              bigserial PRIMARY KEY,
    ts              timestamptz NOT NULL DEFAULT now(),
    wallet_address  text NOT NULL,
    pair_address    text NOT NULL,

    item_name       text,
    price_vee       numeric(38,18),
    reserve_vee     numeric(38,18),
    reserve_item    numeric(38,18),

    lp_balance      numeric(38,18),
    lp_share        numeric(38,18),
    user_vee        numeric(38,18),
    user_item       numeric(38,18),

    volume_24h_vee  numeric(38,18),
    volume_7d_vee   numeric(38,18),
    lp_earn_vee_24h numeric(38,18),
    lp_earn_vee_7d  numeric(38,18),
    lp_apr          numeric(38,18)
);

CREATE INDEX lp_snapshots_wallet_pair_ts_idx
ON lp_snapshots (wallet_address, pair_address, ts);
🧩 Backend (FastAPI)
Start ręczny (dev):

bash
Skopiuj kod
cd /root/gex
uvicorn server:app --host 127.0.0.1 --port 8000 --reload
Endpointy
Endpoint	Opis
GET /api/market	Ostatnie snapshoty wszystkich par + wolumen 24h/7d + price/vol Δ
GET /api/market/{wallet}	Jak wyżej + LP usera (udział, fees 24h/7d, APR est.)
GET /api/history/{pair}	Historia ceny/rezerw + dzienny wolumen VEE dla pary
GET /api/lp/{wallet}	Ostatnie snapshoty LP z lp_snapshots (po 1 na parę)
GET /api/lp/history7/{wallet}	Historia LP z 7 dni (opcjonalnie filtrowana po pair=)
GET /api/lp/history30/{wallet}	Historia LP z 30 dni (opcjonalnie filtrowana po pair=)

Frontend:

index.html używa:

GET /api/market

GET /api/market/{wallet}

item.html używa:

GET /api/market

GET /api/market/{wallet}

GET /api/history/{pair}

GET /api/lp/history30/{wallet}?pair=... do wykresu LP.

🧵 Ingesty
Snapshoty LP (on-chain rezerwy) – ingest_pairs.py
bash
Skopiuj kod
cd /root/gex
. .venv/bin/activate
python ingest_pairs.py
Zapis do gex_snapshots.

Swap ingest – ingest_trades.py
Czyta tylko nowe bloki dzięki trades_cursor:

bash
Skopiuj kod
cd /root/gex
. .venv/bin/activate
python ingest_trades.py
Wolumen liczony jako (VEE in + VEE out) / 2, zapis do trades_ronin.

Snapshoty LP usera – ingest_lp_snapshots.py
bash
Skopiuj kod
cd /root/gex
. .venv/bin/activate
python ingest_lp_snapshots.py
Zapis do lp_snapshots.

🔁 Full resync (jeśli kiedyś będziesz chciał wszystko od nowa)
Ustaw w .env:

env
Skopiuj kod
TRADES_START_BLOCK=50000000   # przykładowy blok startowy
W bazie (uwaga, kasuje dane z trades!):

sql
Skopiuj kod
DROP TABLE IF EXISTS trades_ronin CASCADE;
DROP TABLE IF EXISTS trades_cursor CASCADE;
Odpal:

bash
Skopiuj kod
cd /root/gex
. .venv/bin/activate
python ingest_trades.py
🚀 Produkcja (VPS)
Backend (systemd)
/etc/systemd/system/gex-backend.service:

ini
Skopiuj kod
[Unit]
Description=Zeeverse GEX backend (Uvicorn)
After=network.target postgresql.service

[Service]
User=root
WorkingDirectory=/root/gex
Environment="PYTHONUNBUFFERED=1"
ExecStart=/root/gex/.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
Ingest trades (co 30 min)
/etc/systemd/system/gex-trades.service:

ini
Skopiuj kod
[Service]
Type=oneshot
WorkingDirectory=/root/gex
ExecStart=/root/gex/.venv/bin/python3 /root/gex/ingest_trades.py
/etc/systemd/system/gex-trades.timer:

ini
Skopiuj kod
[Unit]
Description=Run Zeeverse GEX trades ingest every 30 minutes

[Timer]
OnBootSec=30
OnUnitActiveSec=1800
Unit=gex-trades.service

[Install]
WantedBy=timers.target
LP snapshots (co godzinę)
/etc/systemd/system/gex-lp.service:

ini
Skopiuj kod
[Service]
Type=oneshot
WorkingDirectory=/root/gex
ExecStart=/root/gex/.venv/bin/python3 /root/gex/ingest_lp_snapshots.py
/etc/systemd/system/gex-lp.timer:

ini
Skopiuj kod
[Unit]
Description=LP snapshot every 60 minutes

[Timer]
OnBootSec=10
OnUnitActiveSec=3600
Unit=gex-lp.service

[Install]
WantedBy=timers.target
🌐 Nginx (produkcyjny routing)
/etc/nginx/sites-available/default:

nginx
Skopiuj kod
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    root /var/www/gex-frontend;
    index index.html;

    # FRONTEND
    location / {
        try_files $uri $uri/ /index.html;
    }

    # BACKEND API -> FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
🚚 Deployment frontendu
Źródło: /root/gex/frontend
Serwowany: /var/www/gex-frontend

Skrypt:

bash
Skopiuj kod
/root/gex/deploy_frontend.sh
Treść:

bash
Skopiuj kod
#!/bin/bash
set -e

echo "[DEPLOY] git pull..."
cd /root/gex
git pull

echo "[DEPLOY] syncing frontend..."
rsync -av --delete /root/gex/frontend/ /var/www/gex-frontend/

echo "[DONE]"
Prawa:

bash
Skopiuj kod
chmod +x /root/gex/deploy_frontend.sh
🧪 Testy po deployu
Backend:

bash
Skopiuj kod
curl -s http://127.0.0.1:8000/api/market | head
Frontend:

bash
Skopiuj kod
curl -s http://127.0.0.1 | head
Nginx:

bash
Skopiuj kod
nginx -t
systemctl reload nginx
tail -n 100 /var/log/nginx/error.log
🛡️ Backup
Backup:

bash
Skopiuj kod
pg_dump -U gex_user gex > gex_backup.sql
Restore:

bash
Skopiuj kod
psql -U gex_user -d gex -f gex_backup.sql