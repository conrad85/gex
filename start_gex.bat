@echo off

REM === API ===
start "GEX API" cmd /k "cd C:\dev\gex_current\gex && call .venv\Scripts\activate.bat && python server.py"

REM === INGEST (jednorazowy) ===
start "GEX INGEST ONCE" cmd /k "cd C:\dev\gex_current\gex && call .venv\Scripts\activate.bat && python ingest_pairs.py"
