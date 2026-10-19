"""
Desktop launcher: runs the local server (app.py) on 127.0.0.1 and opens it in a
native pywebview window. This is the entry point PyInstaller packages.

Run with:
    python launcher.py
"""

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
import webview

from app import app as fastapi_app

HOST = "127.0.0.1"


def find_free_port():
    """Asks the OS for an unused port instead of hardcoding one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def run_server(port):
    """Runs the FastAPI app in this thread."""
    try:
        uvicorn.run(fastapi_app, host=HOST, port=port, log_level="warning")
    except Exception as exc:
        server_errors.append(exc)


def wait_for_server(port, timeout=45):
    """Blocks until the local server is actually accepting requests."""
    url = f"http://{HOST}:{port}/status"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_errors:
            raise RuntimeError(f"Local server failed to start: {server_errors[0]}") from server_errors[0]
        try:
            urllib.request.urlopen(url, timeout=0.5)
            return True
        except Exception:
            time.sleep(0.2)
    return False


BLOCKED_HELP = (
    "C.A.P. couldn't open its window because Windows marked its files as downloaded from the internet.\n\n"
    "To fix it:\n"
    "1. Delete the CAP folder you extracted.\n"
    "2. Right-click the downloaded ZIP, choose Properties, tick Unblock, and click OK.\n"
    "3. Extract the ZIP again into a folder you own, such as Documents, and open CAP.exe.\n\n"
    "Details: {error}"
)


def clear_download_marks():
    """Windows tags every file unzipped from a browser download as coming from the internet (a Zone.Identifier
    stream), and .NET then refuses to load the assemblies that draw the window ("Failed to resolve
    Python.Runtime.Loader.Initialize"). The files are C.A.P.'s own, so drop the tag from its bundled DLLs."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    for dll in root.rglob("*.dll"):
        try:
            os.remove("\\\\?\\" + str(dll) + ":Zone.Identifier")  # \\?\ allows paths past 260 characters
        except OSError:
            pass  # not tagged, or a folder the user can't write to


def check_window_libraries():
    """Loads the libraries that draw the window on this system, without opening one."""
    if sys.platform == "win32":
        from webview.platforms import winforms  # noqa: F401 - loads .NET through pythonnet
    elif sys.platform == "darwin":
        from webview.platforms import cocoa  # noqa: F401
    else:
        from webview.platforms import qt  # noqa: F401


def self_test(port):
    """`CAP --selftest`: proves a packaged build starts, serves its pages and can load its window libraries, without
    opening a window. The release builds run this on Windows, macOS and Linux before anything is published."""
    for path in ("/status", "/", "/ui/app.js", "/ui/styles.css"):
        with urllib.request.urlopen(f"http://{HOST}:{port}{path}", timeout=10) as response:
            if response.status != 200:
                raise SystemExit(f"Self-test failed: {path} returned {response.status}")
    check_window_libraries()
    print("C.A.P. self-test passed")


def show_error(text):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "C.A.P. couldn't start", 0x10)
    else:
        print(text, file=sys.stderr)


def main():
    global server_errors
    server_errors = []
    clear_download_marks()
    port = find_free_port()
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    if not wait_for_server(port):
        raise RuntimeError(
            "Local server didn't start in time. Run app.py directly with "
            "uvicorn to see the underlying error."
        )
    if "--selftest" in sys.argv:
        return self_test(port)

    webview.settings["ALLOW_DOWNLOADS"] = True  # lets "Export my data" save a file
    webview.create_window(
        "C.A.P. — Inbox Assistant",
        f"http://{HOST}:{port}",
        width=1280,
        height=840,
        min_size=(900, 620),
    )
    webview.start()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # A windowed build would show an error dialog and hang a build server; exit with a code instead.
        try:
            main()
        except BaseException as exc:  # noqa: BLE001 - any failure is a failed self-test
            print(f"C.A.P. self-test failed: {exc}", file=sys.stderr)
            os._exit(1)
        os._exit(0)
    try:
        main()
    except RuntimeError as exc:
        if "Python.Runtime" not in str(exc):
            raise
        show_error(BLOCKED_HELP.format(error=exc))
        os._exit(1)
