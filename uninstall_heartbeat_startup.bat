@echo off
setlocal
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MT5 Heartbeat.lnk"
if exist "%LNK%" (
  del "%LNK%"
  if errorlevel 1 (
    echo Failed to remove Startup shortcut:
    echo   "%LNK%"
    exit /b 1
  )
  echo Removed Startup shortcut:
  echo   "%LNK%"
) else (
  echo Startup shortcut not found:
  echo   "%LNK%"
)
endlocal
