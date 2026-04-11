@echo off
REM ---------------------------------------------------------------------------
REM packaging/windows/build.bat
REM Builds KindleTablet.exe for Windows using PyInstaller.
REM
REM Usage (run from repo root):
REM   packaging\windows\build.bat
REM
REM Output: dist\KindleTablet\KindleTablet.exe
REM ---------------------------------------------------------------------------

setlocal EnableDelayedExpansion

echo =^> Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://python.org
    pause & exit /b 1
)

echo =^> Installing/upgrading build dependencies...
pip install --quiet --upgrade pip
pip install --quiet pyinstaller Pillow pystray
pip install --quiet -e ".[all]"

echo =^> Generating app.ico...
python packaging\windows\make_icon.py
if errorlevel 1 (
    echo WARNING: Could not generate icon. Continuing without custom icon.
)

echo =^> Cleaning previous dist...
if exist dist\KindleTablet rmdir /s /q dist\KindleTablet
if exist dist\KindleTablet.exe del /f /q dist\KindleTablet.exe

echo =^> Running PyInstaller...
pyinstaller packaging\windows\kindle_tablet.spec --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller failed.
    pause & exit /b 1
)

echo.
echo [OK]  Build complete!
echo       Executable: dist\KindleTablet\KindleTablet.exe
echo.
echo To create a portable ZIP:
echo   powershell Compress-Archive dist\KindleTablet dist\KindleTablet.zip
echo.
pause
