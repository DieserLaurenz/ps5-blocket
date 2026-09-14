@echo off
cd /d "%~dp0"
if not exist ".watch-venv\Scripts\python.exe" (
  echo First run: python -m venv .watch-venv
  echo Then: .watch-venv\Scripts\python.exe -m pip install -r requirements-watch.txt
  pause
  exit /b 1
)
.watch-venv\Scripts\python.exe watch_monitor.py --dry-run
if errorlevel 1 (
  echo Watch check failed. Check output\watch-offers.json for source status.
  pause
  exit /b 1
)
start "" "output\watch-offers.html"
