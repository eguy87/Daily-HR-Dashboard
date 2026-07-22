"""Serve the generated dashboard on localhost using only the Python stdlib."""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve docs/ as the local dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind")
    parser.add_argument("--port", type=int, default=8000, help="port to use")
    parser.add_argument("--open", action="store_true", help="open the browser")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not (DOCS / "index.html").exists():
        raise SystemExit("docs/index.html is missing. Run `python app/update.py` first.")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DOCS)
    url = f"http://{args.host}:{args.port}/"
    with ReusableThreadingTCPServer((args.host, args.port), handler) as server:
        print(f"HR League Dashboard is running at {url}")
        print("Press Ctrl+C to stop.")
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

