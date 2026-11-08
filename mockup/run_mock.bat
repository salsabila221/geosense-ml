@echo off
cd /d "%~dp0"
python run_mock.py --open
if errorlevel 1 pause
