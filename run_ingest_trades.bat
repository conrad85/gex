@echo off
cd /d C:\dev\gex_current\gex
call .venv\Scripts\activate.bat
python ingest_trades.py
pause
