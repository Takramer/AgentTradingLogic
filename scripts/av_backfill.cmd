@echo off
REM Daily AlphaVantage earnings backfill for the SUE gate.
REM Registered as Windows Scheduled Task "AgentTradingLogic-AVBackfill"
REM (see scripts\register_backfill_task.ps1). Spends up to 20 of the 25/day
REM free-tier calls on the stalest/most-liquid PEAD-signaler tickers, then
REM exits. Resumable: coverage climbs a little each day until the target is
REM fully cached (~26 days for the default 500-name target).
REM
REM Logs each run to av_cache\backfill.log. Run manually any time:
REM   scripts\av_backfill.cmd

setlocal
REM Repo root = this script's parent directory.
set "REPO=%~dp0.."
cd /d "%REPO%"

if not exist "av_cache" mkdir "av_cache"
echo [%DATE% %TIME%] backfill run start >> "av_cache\backfill.log"
py -m rhengine.backfill --once --max-requests 20 >> "av_cache\backfill.log" 2>&1
echo [%DATE% %TIME%] backfill run end (exit %ERRORLEVEL%) >> "av_cache\backfill.log"
endlocal
