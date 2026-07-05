@echo off
REM Builds a standalone Windows .exe for the new Field Force app.
REM Run this ONCE (or again after any code update) on a Windows PC with
REM Python installed. Produces dist\FieldForceApp.exe - a single file that
REM runs with no separate Python installation needed on the machine you
REM copy it to afterward.
REM
REM IMPORTANT: this folder (field_force_app) must sit next to the
REM desktop_client folder, exactly as it does in the delivered zip - this
REM app reuses desktop_client/engines/'s already-tested business logic
REM rather than duplicating it, so both folders need to be present when
REM building.
REM
REM Requirements to RUN this script (one-time setup):
REM   - Python 3.10+ installed from https://python.org, with
REM     "Add python.exe to PATH" checked during install.
REM
REM Usage: double-click this file, or run it from a command prompt in this
REM folder: build_exe.bat

echo === Field Force Optimizer - nova appka: baleni do .exe ===
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo Python nebyl nalezen. Nainstaluj Python z https://python.org
    echo a pri instalaci zaskrtni "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist "..\desktop_client\engines" (
    echo CHYBA: slozka desktop_client neni vedle field_force_app.
    echo Tahle appka pouziva uz otestovanou logiku z desktop_client/engines -
    echo rozbal cely zip tak, aby desktop_client a field_force_app byly
    echo vedle sebe ve stejne nadrazene slozce.
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
REM --paths ..: aby PyInstaller nasel desktop_client/engines (repo-root
REM slozka o uroven vys, viz app.py's vlastni sys.path.insert stejneho cile).
REM --hidden-import PIL._tkinter_finder: stejny fix jako u desktop_client's
REM build_exe.bat - ttkbootstrap vykresluje scrollbary pres Pillow a
REM PyInstalleruv automaticky PIL hook tenhle submodul nenajde sam.
python -m PyInstaller --onefile --windowed --noconfirm ^
    --name FieldForceApp ^
    --paths .. ^
    --hidden-import PIL._tkinter_finder ^
    app.py

if errorlevel 1 (
    echo Baleni selhalo - viz chybova hlaska vyse.
    pause
    exit /b 1
)

echo.
echo === Hotovo ===
echo Aplikace je pripravena zde: dist\FieldForceApp.exe
echo Tento jeden soubor muzes zkopirovat kamkoliv a spoustet dvojklikem -
echo Python uz na cilovem pocitaci potreba neni.
pause
