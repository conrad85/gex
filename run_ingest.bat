@echo off
cd C:\dev\gex_current\gex
call .venv\Scripts\activate.bat

REM upewnij się że folder logs istnieje:
if not exist logs mkdir logs

python ingest_pairs.py >> logs\ingest.log 2>&1
