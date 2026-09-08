@echo off
cd /d "%~dp0"
python scraper.py --watch 900 --open
if errorlevel 1 pause
