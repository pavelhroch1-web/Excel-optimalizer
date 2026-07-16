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

# A --windowed PyInstaller app has NO console: sys.stdout/stderr are None.
# Libraries that probe the terminal (uvicorn's logger calls sys.stdout.isatty())
# then crash. Give them a real, harmless stream before anything imports them.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        try:
            setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
        except Exception:  # noqa: BLE001
            pass

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
    # Seed scaffold: bundled at _MEIPASS/workbook/... . Several modules compute
    # its path from __file__ (dirname(dirname())), which is WRONG when frozen
    # (modules sit flat in _MEIPASS). Point every consumer at the bundled file
    # via the env overrides they already honor.
    try:
        _scaffold = os.path.join(BASE_DIR, "workbook", "FieldForceOptimizer_V11_scaffold.xlsx")
        if os.path.exists(_scaffold):
            os.environ.setdefault("WORKBOOK_PATH", _scaffold)
            os.environ.setdefault("CONFIG_SEED_WORKBOOK", _scaffold)
        _sample = os.path.join(BASE_DIR, "sample_data")
        if os.path.isdir(_sample):
            os.environ.setdefault("SAMPLE_DATA_DIR", _sample)
    except Exception:  # noqa: BLE001
        pass


def _log_startup_error(msg: str) -> None:
    """Frozen --windowed apps show nothing on crash. Write the real error to a
    file next to the app so it can be inspected/sent."""
    try:
        import db as _db
        path = os.path.join(_db.data_dir(), "startup_error.log")
    except Exception:  # noqa: BLE001
        path = os.path.join(os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                                            else __file__), "startup_error.log")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{msg}\n")
    except Exception:  # noqa: BLE001
        pass
    return path


HOST = "127.0.0.1"
_SERVE_ERROR: list = []


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
    try:
        import uvicorn

        from main import app  # noqa: WPS433 - after sys.path + env are ready
        # log_config=None: skip uvicorn's colour formatter (it calls
        # sys.stdout.isatty(), which crashes in a windowed .exe).
        uvicorn.run(app, host=HOST, port=port, log_level="warning", log_config=None)
    except BaseException as exc:  # noqa: BLE001 - capture so the UI can show it
        import traceback
        _SERVE_ERROR.append(traceback.format_exc())
        _log_startup_error(f"Server thread selhal:\n{traceback.format_exc()}")
        raise


def _show_fatal(message: str, log_path: str) -> None:
    """Last-resort visible error (frozen --windowed shows nothing otherwise)."""
    try:
        import tkinter as tk
        from tkinter import scrolledtext
        root = tk.Tk()
        root.title("Field Force Optimizer — chyba při startu")
        root.geometry("760x460")
        tk.Label(root, justify="left", padx=14, pady=10, anchor="w",
                 text=("Aplikaci se nepodařilo nastartovat.\n"
                       f"Detail chyby byl uložen do:\n{log_path}\n\n"
                       "Pošli prosím tento soubor / text níže.")).pack(fill="x")
        box = scrolledtext.ScrolledText(root, wrap="word")
        box.insert("1.0", message or "(bez detailu)")
        box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        root.mainloop()
    except Exception:  # noqa: BLE001
        print(message, file=sys.stderr)


def main() -> None:
    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    if not _wait_until_up(port):
        detail = _SERVE_ERROR[0] if _SERVE_ERROR else "Server nenaběhl v časovém limitu."
        log_path = _log_startup_error(f"Server se nenastartoval do limitu.\n{detail}")
        _show_fatal(detail, log_path)
        sys.exit(1)

    url = f"http://{HOST}:{port}/"
    # Preferováno: nativní okno přes pywebview (WebView2). Na zamčeném firemním
    # PC ale WebView2 nemusí být — pak BEZ jakékoli instalace spadneme na
    # výchozí prohlížeč (ten má každý Windows) + malé kontrolní okno (tkinter je
    # ve standardní knihovně Pythonu, zabalí se do .exe, nic se neinstaluje).
    try:
        import webview  # pywebview -> WebView2
        webview.create_window("Field Force Optimizer", url,
                              width=1280, height=860, min_size=(900, 600))
        webview.start()
        return
    except Exception:  # noqa: BLE001 - WebView2 chybí / selhalo
        pass
    _run_in_browser(url)


def _run_in_browser(url: str) -> None:
    """Zero-install fallback: otevři výchozí prohlížeč a drž aplikaci naživu
    malým oknem, kterým ji lze ukončit. Vše jen ze standardní knihovny."""
    import webbrowser
    webbrowser.open(url)
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title("Field Force Optimizer")
        tk.Label(root, justify="center", padx=24, pady=18,
                 text=("Field Force Optimizer běží.\n\nAplikace je otevřená v prohlížeči:\n"
                       f"{url}\n\nToto okno nechte otevřené — jeho zavřením\naplikaci ukončíte.")
                 ).pack()
        tk.Button(root, text="Otevřít znovu v prohlížeči",
                  command=lambda: webbrowser.open(url)).pack(pady=(0, 16))
        root.mainloop()
    except Exception:  # noqa: BLE001 - i tkinter nedostupné: drž běh
        print(f"UI běží na {url} — aplikaci ukončíš zavřením tohoto okna.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
