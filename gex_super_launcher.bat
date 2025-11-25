@echo off
REM ==========================================
REM  GEX SUPER LAUNCHER
REM  - jeden plik do wszystkiego
REM  - włącza API z auto-restartem
REM  - odpala jednorazowy ingest w osobnym oknie
REM ==========================================

REM Jeśli wywołane z parametrem, skaczemy do odpowiedniej sekcji
if "%1"=="api" goto run_api
if "%1"=="ingest" goto run_ingest

REM === GŁÓWNE WEJŚCIE ===
set GEX_DIR=C:\dev\gex_current\gex

echo [MAIN] Startuję GEX API (auto-restart) i jednorazowy ingest...
echo [MAIN] Katalog projektu: %GEX_DIR%
echo.

REM Okno API z auto-restartem
start "GEX API (auto-restart)" "%~f0" api

REM Okno z jednorazowym ingestem
start "GEX INGEST ONCE" "%~f0" ingest

echo [MAIN] Wszystko odpalone. To okno możesz zamknąć.
goto :eof


:run_api
REM === PĘTLA API Z AUTO-RESTARTEM ===
set GEX_DIR=C:\dev\gex_current\gex
cd /d %GEX_DIR%

echo [API] Katalog: %CD%
call .venv\Scripts\activate.bat

:api_loop
echo [API] Uruchamiam server.py...
python server.py
echo [API] Zakończono z kodem %errorlevel%.

echo [API] Restart za 5 sekund (zamknij okno, jeśli nie chcesz restartu)...
timeout /t 5 >nul
goto api_loop


:run_ingest
REM === JEDNORAZOWY INGEST PAR ===
set GEX_DIR=C:\dev\gex_current\gex
cd /d %GEX_DIR%

echo [INGEST] Katalog: %CD%
call .venv\Scripts\activate.bat

REM folder na logi
if not exist logs mkdir logs

echo [INGEST] Odpalam ingest_pairs.py...
python ingest_pairs.py >> logs\ingest.log 2>&1
echo [INGEST] Zakończono. Log: logs\ingest.log
pause
goto :eof
