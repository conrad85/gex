# GEX – podgląd i ingest rynku (Ronin)

## Kluczowe pliki
- `server.py` – API FastAPI (snapshoty + on-chain wolumen z RPC).
- `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` – prosty UI pobierający dane z API.
- `ingest_pairs.py` + `run_ingest.bat` – zrzuca snapshoty par do tabeli `gex_snapshots` (log w `logs/ingest.log`).
- `ingest_trades.py` + `run_ingest_trades.bat` – zaciąga eventy `Swap` po 10 bloków (limit free Alchemy) i zapisuje do `trades_ronin` z kursorem w `trades_cursor`.
- `trades_schema.sql` – schema `trades_ronin` i `trades_cursor`.
- `gex_pairs_seed.sql` – seed znanych par.
- `requirements.txt`, `.env` – zależności i konfiguracja (DB, RPC, start block dla trades).

## Szybki start
1) **Zależności** (Python 3.11+):
   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2) **Konfiguracja `.env`** (już w repo, uzupełnij):
   - `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASS`
   - `RONIN_RPC` (np. Alchemy): wymagane do API i ingestów
   - `TRADES_START_BLOCK` (opcjonalne, tylko pierwsze uruchomienie trades ingest; brak = latest-5000)
3) **Schema trades**: uruchom `trades_schema.sql` w swojej bazie (pgAdmin/psql).
4) **Seed par (opcjonalnie)**: `gex_pairs_seed.sql` do tabeli par/snapshotów, jeśli potrzebujesz startowego zestawu.

## Uruchamianie
- **Ingest snapshotów**: `run_ingest.bat` (tworzy `logs/ingest.log`) albo `python ingest_pairs.py`.
- **Ingest swapów** (10-blokowe okna, free-tier Alchemy): `run_ingest_trades.bat` albo `python ingest_trades.py`.
  - Wymaga tabel `trades_ronin`, `trades_cursor` i działającego RPC.
  - Uprawnienia: jeśli nie jesteś ownerem tabel/seq, skrypt pomija tworzenie indeksów i używa ręcznego `id`.
- **API**: `python server.py` lub `uvicorn server:app --reload` (domyślnie `127.0.0.1:8000`).
- **Frontend**: otwórz `frontend/index.html` (pobiera z `http://127.0.0.1:8000/api/market`).

## Uwaga nt. wolumenu
- Obecnie `server.py` liczy wolumen z logów bezpośrednio przez RPC (limit free Alchemy = 10 bloków; kosztowne).
- `ingest_trades.py` zapisuje swapowe wolumeny do DB – po potwierdzeniu działania można podpiąć API pod `trades_ronin` zamiast RPC.
