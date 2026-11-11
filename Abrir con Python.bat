@echo off
rem Doble clic para abrir Pano360 Downloader (con Python, sin el .exe)
cd /d "%~dp0"
python -c "import requests, PIL" 2>nul || python -m pip install -r requirements.txt
start "" pythonw app.py
