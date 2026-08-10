@echo off
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo python not found on PATH. Install Python 3.10+ or fix PATH, then retry.
  pause
  exit /b 1
)
echo Starting mt5_heartbeat.py from "%CD%"
python mt5_heartbeat.py
if errorlevel 1 (
  echo Heartbeat exited with error %ERRORLEVEL%
  pause
)
