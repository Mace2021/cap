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


def self_test(port):
    """`CAP --selftest`: proves a packaged build starts and serves its pages, without opening a window.
    The release builds run this on Windows, macOS and Linux before anything is published."""
    for path in ("/status", "/", "/ui/app.js", "/ui/styles.css"):
        with urllib.request.urlopen(f"http://{HOST}:{port}{path}", timeout=10) as response:
            if response.status != 200:
                raise SystemExit(f"Self-test failed: {path} returned {response.status}")
    print("C.A.P. self-test passed")


def main():
    global server_errors
    server_errors = []
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
    main()
