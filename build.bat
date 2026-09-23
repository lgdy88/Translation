@echo off
REM Rebuild translator.exe with PyInstaller (same layout as the original binary)
REM Original: PyInstaller onefile, Python 3.11, windowed (no console)
pip install pyinstaller
pyinstaller --onefile --noconsole --name translator translator.py
echo.
echo Output: dist\translator.exe
