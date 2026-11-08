@echo off
cd /d "%~dp0"
echo Looping event tanpa mengubah status. Tekan Ctrl+C untuk berhenti.
python publish_mock_mqtt.py --condition event --loop --interval 10
pause
