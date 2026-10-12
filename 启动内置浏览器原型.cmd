@echo off
cd /d "%~dp0"
if not exist "native\bin\WebViewHost.exe" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "native\build_webview2.ps1"
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
start "逐页内置浏览器原型" ".venv\Scripts\pythonw.exe" "tools\webview2_prototype.py"
