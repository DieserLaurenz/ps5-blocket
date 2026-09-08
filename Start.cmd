@echo off
cd /d "%~dp0"
python scraper.py --open
if errorlevel 1 pause
