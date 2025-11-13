@echo off
start "GEX API" cmd /k "call C:\dev\gex\.venv\Scripts\activate.bat && python C:\dev\gex\server.py"
start "GEX INGEST ONCE" cmd /k "call C:\dev\gex\.venv\Scripts\activate.bat && python C:\dev\gex\ingest_pairs.py"
