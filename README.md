# GEX – Zeeverse Market Monitor (Ronin)

Monitor rynku Zeeverse (Ronin GEX):
- snapshoty LP par (rezerwy itemów i VEE),
- ingest swapów on-chain (24h wolumen),
- REST API FastAPI,
- prosty frontend HTML/JS,
- automatyzacja ingestów przez systemd na VPS.

---

## 📦 Struktura projektu

gex/
│
├── server.py # API: snapshots + LP + 24h volume
├── ingest_pairs.py # snapshoty rezerw LP -> gex_snapshots
├── ingest_trades.py # swap event ingest -> trades_ronin
│
├── gex_pairs_seed.sql # seed par LP
├── trades_schema.sql # schema trades_ronin + trades_cursor
│
├── frontend/
│ ├── index.html
│ ├── app.js
│ └── styles.css
│
├── run_ingest.bat
├── run_ingest_trades.bat
├── gex_super_launcher.bat
│
└── requirements.txt


---

## ⚙️ Wymagania

- Python **3.10+**
- PostgreSQL **15+**
- RPC Ronin (np. Alchemy)
- Brak zależności Node – frontend to czysty JS

---

## 🔧 Instalacja lokalna (dev)

### 1. Zależności

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
2. Plik .env
ini
Skopiuj kod
DB_HOST=localhost
DB_PORT=5432
DB_NAME=gex
DB_USER=gex_user
DB_PASS=<hasło>

RONIN_RPC=https://ronin-mainnet.g.alchemy.com/v2/<klucz>

# tylko pierwsze uruchomienie ingest_trades
# TRADES_START_BLOCK=50000000
3. Utwórz bazę
sql
Skopiuj kod
CREATE DATABASE gex;
CREATE USER gex_user WITH PASSWORD 'gex_pass';
GRANT ALL PRIVILEGES ON DATABASE gex TO gex_user;
4. Wgraj schemy
bash
Skopiuj kod
psql -U gex_user -d gex -f trades_schema.sql
psql -U gex_user -d gex -f gex_pairs_seed.sql
📊 Ingest danych
Snapshoty LP
bash
Skopiuj kod
python ingest_pairs.py
Ingest swapów (wolumen 24h)
bash
Skopiuj kod
python ingest_trades.py
Skrypt zapisuje stan bloku w trades_cursor, więc pobiera tylko nowe dane.

🖥️ Backend API
Start:

bash
Skopiuj kod
uvicorn server:app --host 127.0.0.1 --port 8000 --reload
Endpointy
Endpoint	Opis
/api/market	snapshoty + wolumen 24h
/api/market/{wallet}	snapshoty + LP usera
/api/debug/volume/{pair}	szczegółowy debug liczenia vol

🌐 Frontend
Plik:

bash
Skopiuj kod
frontend/index.html
Konfiguracja walleta:

bash
Skopiuj kod
frontend/app.js
Domyślny endpoint:

php-template
Skopiuj kod
http://<host>:8000/api/market/<wallet>
🚀 Produkcja (VPS)
Backend – systemd
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
ExecStart=/root/gex/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
Aktywacja:

bash
Skopiuj kod
systemctl daemon-reload
systemctl enable --now gex-backend
Ingest swapów – systemd timer
/etc/systemd/system/gex-trades.service:

ini
Skopiuj kod
[Service]
Type=oneshot
WorkingDirectory=/root/gex
ExecStart=/root/gex/.venv/bin/python3 /root/gex/ingest_trades.py
Timer:

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
Uruchomienie:

bash
Skopiuj kod
systemctl daemon-reload
systemctl enable --now gex-trades.timer
🗄️ Tabele w bazie
gex_snapshots
Kolumna	Opis
pair_address	LP
item_name	nazwa itemu
reserve_item	token0 LP
reserve_vee	token1 LP
price_vee	cena item→VEE
ts	timestamp

trades_ronin
Kolumna	Opis
pair_address	LP
block_number	blok
tx_hash	hash
vee_amount	ilość VEE w swapie
ts	timestamp bloku

🔁 Backup & restore
Backup:
bash
Skopiuj kod
pg_dump -U gex_user gex > gex_backup.sql
Restore:
bash
Skopiuj kod
psql -U gex_user -d gex -f gex_backup.sql
📝 Uwagi
ingest_pairs generuje snapshoty co uruchomienie.

ingest_trades pobiera tylko nowe bloki dzięki trades_cursor.

API korzysta już z bazy zamiast RPC dla wolumenu (wydajność).

Projekt działa w pełni offline poza RPC.

ℹ️ Autor
Repozytorium prywatne.
Użytek własny do analizy rynku Zeeverse.