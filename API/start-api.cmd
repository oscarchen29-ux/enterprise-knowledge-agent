@echo off
rem Start the API server (FastAPI) on all interfaces, port 7000, so LAN devices can call it directly.
rem The API trusts the X-Student-ID header: anyone who can reach it can act as any student. Test environments only.
cd /d "%~dp0"
echo.
echo   API docs on this PC: http://127.0.0.1:7000/docs
powershell -NoProfile -Command "Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | ForEach-Object { '  From LAN devices:    http://' + $_.IPv4Address.IPAddress + ':7000/docs   (' + $_.InterfaceAlias + ')' }"
echo   Uvicorn prints http://0.0.0.0:7000 below - browsers cannot open that address, use the ones above.
echo.
python -m uvicorn app.main:app --host 0.0.0.0 --port 7000 %*
