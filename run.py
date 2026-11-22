"""Uruchomienie ChessPrep.

    python run.py            - okno aplikacji (pywebview)
    python run.py --server   - sam serwer (otwórz http://127.0.0.1:8777 w przeglądarce)
"""
from __future__ import annotations

import argparse
import socket
import threading
import time

import uvicorn

from chessprep.config import CONFIG


def _wait_port(port: int, timeout: float = 15.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", action="store_true", help="bez okna, tylko serwer HTTP")
    ap.add_argument("--port", type=int, default=CONFIG["port"])
    args = ap.parse_args()

    from chessprep.server import app

    if args.server:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        return

    import webview

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    _wait_port(args.port)
    webview.create_window("ChessPrep", f"http://127.0.0.1:{args.port}/", width=1500, height=920,
                          min_size=(1100, 700))
    webview.start()
    server.should_exit = True
    from chessprep import engine
    engine.shared().close()


if __name__ == "__main__":
    main()
