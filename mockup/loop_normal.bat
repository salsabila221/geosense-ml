@echo off
cd /d "%~dp0"
echo Looping getaran normal (AMAN). Tekan Ctrl+C untuk berhenti.
python publish_mock_mqtt.py --condition normal --loop --interval 10
pause
