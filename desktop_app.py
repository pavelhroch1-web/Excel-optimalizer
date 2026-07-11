"""
Field Force Optimizer - desktopová aplikace (portable .exe).

Spustí lokální FastAPI (127.0.0.1) v pozadí a otevře tvoje stávající webové
UI v nativním okně přes pywebview. Vše běží LOKÁLNĚ na tomto PC:
  React/JS UI  ->  FastAPI (localhost)  ->  Planning Engine / Field Brain
                                         ->  SQLite (FieldForceData/)
Žádný Render, žádné GitHub Actions, žádný cloud. Excel je jen import/export.

Data (SQLite + snapshoty) se ukládají do složky FieldForceData vedle .exe -
appka je přenositelná, funguje bez instalace (i z USB).

Spuštění ze zdroje:  python3 desktop_app.py
Build portable .exe:  desktop_client/build_desktop_exe.bat  (na Windows)
"""
import os
import socket
import sys
import threading
import time

# --- import path (dev i frozen) --------------------------------------------
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS  # noqa: SLF001
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
for p in (BASE_DIR, os.path.join(BASE_DIR, "backend"),
          os.path.join(BASE_DIR, "tools"), os.path.join(BASE_DIR, "desktop_client")):
    if p not in sys.path:
        sys.path.insert(0, p)

# Musí být nastaveno PŘED importem main (přepíná úložiště na SQLite, vypíná auth).
os.environ.setdefault("FFO_LOCAL", "1")

# Portable: udrž VŠECHNY zápisy (vč. dočasných souborů) uvnitř pracovní složky
# aplikace, ať nic nepíše mimo ni (žádné %TEMP%, žádná admin práva, žádná
# instalace). Data + tmp jsou vedle .exe ve FieldForceData/.
if getattr(sys, "frozen", False):
    try:
        import db as _db  # backend/db.py je už na sys.path
        _tmp = os.path.join(_db.data_dir(), "tmp")
        os.makedirs(_tmp, exist_ok=True)
        import tempfile as _tempfile
        _tempfile.tempdir = _tmp
        os.environ["TMP"] = os.environ["TEMP"] = os.environ["TMPDIR"] = _tmp
    except Exception:  # noqa: BLE001 - nikdy nebránit startu appky
        pass

HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _wait_until_up(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((HOST, port)) == 0:
                return True
        time.sleep(0.15)
    return False


def _serve(port: int) -> None:
    import uvicorn

    from main import app  # noqa: WPS433 - after sys.path + env are ready
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


def main() -> None:
    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    if not _wait_until_up(port):
        print("Server se nepodařilo nastartovat.", file=sys.stderr)
        sys.exit(1)

    url = f"http://{HOST}:{port}/"
    try:
        import webview  # pywebview
        webview.create_window("Field Force Optimizer", url,
                              width=1280, height=860, min_size=(900, 600))
        webview.start()
    except Exception:
        # Nouzově: když pywebview není k dispozici, otevři v prohlížeči.
        import webbrowser
        webbrowser.open(url)
        print(f"UI běží na {url} — okno zavři ukončením tohoto programu.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
