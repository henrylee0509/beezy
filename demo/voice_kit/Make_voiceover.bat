@echo off
setlocal
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" (
  where pwsh >nul 2>nul && set "PS=pwsh"
)
if not exist "%PS%" if not "%PS%"=="pwsh" (
  echo Could not find PowerShell on this PC. Please tell Claude.
  pause
  exit /b 1
)
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_voice.ps1"
pause
