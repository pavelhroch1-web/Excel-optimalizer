@echo off
REM ==========================================================================
REM  Field Force Optimizer - Tour Plan Generator: build a standalone .exe
REM ==========================================================================
REM  Spusti TOHLE na Windows PC, kde je nainstalovany Python (jednou; nebo
REM  znovu po kazde uprave kodu). Vyrobi jeden soubor:
REM      dist\FieldForceTourPlan.exe
REM  ten pak muzes zkopirovat kamkoliv a spoustet dvojklikem - Python uz na
REM  cilovem PC potreba neni.
REM
REM  Predpoklad: Python 3.10+ z https://python.org, pri instalaci zaskrtnout
REM  "Add python.exe to PATH".
REM
REM  Pouziti: dvojklik na tento soubor (je ve slozce desktop_client).
REM ==========================================================================

echo === Tour Plan Generator: stavim .exe ===
echo.

REM Prepni se do KORENE repozitare (o slozku vys nez tento .bat).
pushd "%~dp0\.."

python --version >nul 2>&1
if errorlevel 1 (
    echo Python nebyl nalezen. Nainstaluj Python z https://python.org
    echo a pri instalaci zaskrtni "Add python.exe to PATH".
    popd & pause & exit /b 1
)

echo Instaluji knihovny ^(openpyxl, ttkbootstrap, pyinstaller^)...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet openpyxl ttkbootstrap pyinstaller
if errorlevel 1 (
    echo Instalace knihoven selhala. Zkontroluj pripojeni k internetu.
    popd & pause & exit /b 1
)

echo.
echo Balim aplikaci do jednoho .exe ^(muze trvat minutu^)...
python -m PyInstaller --onefile --windowed --noconfirm ^
    --name FieldForceTourPlan ^
    --paths backend --paths tools --paths desktop_client --paths . ^
    --collect-all ttkbootstrap ^
    --hidden-import PIL._tkinter_finder ^
    --add-data "workbook\FieldForceOptimizer_V11_scaffold.xlsx;workbook" ^
    desktop_client\tourplan_app.py

if errorlevel 1 (
    echo Baleni selhalo - viz chybova hlaska vyse.
    popd & pause & exit /b 1
)

echo.
echo === Hotovo ===
echo Aplikace je zde: dist\FieldForceTourPlan.exe
echo Tento jeden soubor zkopiruj kamkoliv a spoustej dvojklikem.
popd
pause
