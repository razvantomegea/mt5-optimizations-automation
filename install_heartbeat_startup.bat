@echo off
setlocal
cd /d "%~dp0"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MT5 Heartbeat.lnk"
set "TARGET=%~dp0start_mt5_heartbeat.bat"
set "WORKDIR=%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo python not found on PATH. Install Python 3.10+ or fix PATH, then retry.
  exit /b 1
)

powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath = $env:TARGET; $s.WorkingDirectory = $env:WORKDIR; $s.Save()"
if errorlevel 1 (
  echo Failed to create Startup shortcut.
  exit /b 1
)
echo Installed Startup shortcut:
echo   %LNK%
echo Target:
echo   %TARGET%
endlocal
