@echo off
cd /d "%~dp0"
start "" cmd.exe /k "venv\Scripts\python.exe app.py"
timeout /t 2 >nul
start http://127.0.0.1:5000
exit
