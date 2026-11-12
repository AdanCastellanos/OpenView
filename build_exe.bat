@echo off
rem Vuelve a crear "OpenView.exe" (solo hace falta si cambias el código)
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install --quiet pyinstaller requests pillow pywebview numpy OpenEXR opencv-python-headless
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "OpenView" ^
  --icon "%~dp0assets\icon.ico" ^
  --add-data "%~dp0ui;ui" ^
  --paths "%~dp0." --hidden-import openview ^
  --hidden-import hdri --hidden-import extras --hidden-import OpenEXR --hidden-import cv2 --exclude-module streetlevel --exclude-module tkinter.test ^
  --additional-hooks-dir "%~dp0assets" ^
  --distpath "%~dp0." --workpath "%~dp0build" --specpath "%~dp0build" ^
  app.py
if errorlevel 1 (
  echo.
  echo ERROR: no se pudo crear el .exe
  exit /b 1
)
echo.
echo Listo: "OpenView.exe"