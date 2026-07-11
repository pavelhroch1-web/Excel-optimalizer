@echo off
REM ==========================================================================
REM  Field Force Optimizer - PORTABLE desktop app: build one .exe
REM ==========================================================================
REM  Vyrobi:  dist\FieldForceOptimizer.exe
REM  - portable: zkopiruj kamkoli a spoustej dvojklikem, BEZ instalace.
REM  - data (SQLite + snapshoty) se ulozi do slozky FieldForceData vedle .exe.
REM  - vse bezi lokalne (localhost), zadny Render ani GitHub Actions.
REM
REM  Predpoklad: Python 3.10+ z https://python.org ("Add python.exe to PATH").
REM  Na Windows 10/11 je WebView2 runtime obvykle uz nainstalovany (pro okno
REM  aplikace). Kdyby ne, staci "Microsoft Edge WebView2 Runtime" (zdarma).
REM
REM  Pouziti: dvojklik na tento soubor (je ve slozce desktop_client).
REM ==========================================================================

echo === Field Force Optimizer (desktop): stavim portable .exe ===
echo.

REM Prepni se do KORENE repozitare (o slozku vys nez tento .bat).
pushd "%~dp0\.."

python --version >nul 2>&1
if errorlevel 1 (
    echo Python nebyl nalezen. Nainstaluj Python z https://python.org
    echo a pri instalaci zaskrtni "Add python.exe to PATH".
    popd & pause & exit /b 1
)

echo Instaluji knihovny...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r desktop_client\requirements-desktop.txt pyinstaller
if errorlevel 1 (
    echo Instalace knihoven selhala. Zkontroluj pripojeni k internetu.
    popd & pause & exit /b 1
)

echo.
echo Balim aplikaci do PORTABLE slozky ^(muze trvat par minut^)...
REM --onedir (ne --onefile): vznikne slozka dist\FieldForceOptimizer\ s .exe a
REM podadresarem _internal. Nic se nerozbaluje do %%TEMP%% pri kazdem spusteni
REM (to --onefile dela a na zamcenych firemnich PC to muze byt blokovane/pomale).
REM Slozku staci zazipovat, rozbalit u uzivatele a spustit .exe primo z ni.
python -m PyInstaller --onedir --windowed --noconfirm ^
    --name FieldForceOptimizer ^
    --contents-directory _internal ^
    --paths backend --paths tools --paths desktop_client --paths . ^
    --collect-all uvicorn --collect-all webview ^
    --hidden-import PIL._tkinter_finder ^
    --add-data "web;web" ^
    --add-data "backend\schema.sql;." ^
    --add-data "workbook\FieldForceOptimizer_V11_scaffold.xlsx;workbook" ^
    desktop_app.py

if errorlevel 1 (
    echo Baleni selhalo - viz chybova hlaska vyse.
    popd & pause & exit /b 1
)

REM Zabal portable slozku do ZIP (rozbal a spust - bez instalace, bez admin prav).
echo.
echo Baleni do ZIP...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\FieldForceOptimizer\*' -DestinationPath 'dist\FieldForceOptimizer-portable.zip' -Force" 2>nul

echo.
echo === Hotovo ===
echo Portable slozka: dist\FieldForceOptimizer\  (spoustej FieldForceOptimizer.exe)
echo Portable ZIP:    dist\FieldForceOptimizer-portable.zip
echo.
echo Nasazeni: rozbal ZIP do libovolne slozky (napr. na plose nebo na siti) a
echo spust FieldForceOptimizer.exe. Zadna instalace, zadna admin prava. Vsechna
echo data a konfigurace se ukladaji do slozky FieldForceData\ vedle .exe.
popd
pause
