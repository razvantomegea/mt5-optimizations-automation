@echo off
setlocal
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MT5 Heartbeat.lnk"
if exist "%LNK%" (
  del "%LNK%"
  echo Removed Startup shortcut:
  echo   %LNK%
) else (
  echo Startup shortcut not found:
  echo   %LNK%
)
endlocal
