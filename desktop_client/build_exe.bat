@echo off
REM Builds a standalone Windows .exe for the Distribution Client.
REM Run this ONCE (or again after any code update) on a Windows PC with
REM Python installed. Produces dist\FieldForceDistributionClient.exe - a
REM single file that runs with no separate Python installation needed on
REM the machine you copy it to afterward.
REM
REM Requirements to RUN this script (one-time setup):
REM   - Python 3.10+ installed from https://python.org, with
REM     "Add python.exe to PATH" checked during install.
REM
REM Usage: double-click this file, or run it from a command prompt in this
REM folder: build_exe.bat

echo === Field Force Optimizer - Distribution Client: building .exe ===
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo Python nebyl nalezen. Nainstaluj Python z https://python.org
    echo a pri instalaci zaskrtni "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Instaluji potrebne knihovny ^(openpyxl, ttkbootstrap, pyinstaller^)...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet openpyxl ttkbootstrap pyinstaller
if errorlevel 1 (
    echo Instalace knihoven selhala. Zkontroluj pripojeni k internetu.
    pause
    exit /b 1
)

echo.
echo Balim aplikaci do jednoho .exe souboru...
REM --hidden-import PIL._tkinter_finder: ttkbootstrap renders scrollbars via
REM Pillow, and PyInstaller's automatic PIL hook misses this submodule -
REM without this flag the packaged .exe crashes on startup with
REM ModuleNotFoundError even though it runs fine from source (confirmed by
REM actually running the packaged build, not just the .py source).
python -m PyInstaller --onefile --windowed --noconfirm ^
    --name FieldForceDistributionClient ^
    --hidden-import PIL._tkinter_finder ^
    distribution_client.py

if errorlevel 1 (
    echo Baleni selhalo - viz chybova hlaska vyse.
    pause
    exit /b 1
)

echo.
echo === Hotovo ===
echo Aplikace je pripravena zde: dist\FieldForceDistributionClient.exe
echo Tento jeden soubor muzes zkopirovat kamkoliv a spoustet dvojklikem -
echo Python uz na cilovem pocitaci potreba neni.
pause
