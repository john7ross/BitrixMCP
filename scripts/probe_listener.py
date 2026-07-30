"""Reachability probe: can the Bitrix24 server open a connection to this machine?

Run this on the workstation, then from the Bitrix server run:
    curl -v http://<this-machine-ip>:8000/probe

Anything other than a timeout or "connection refused" means the path exists and
an outgoing webhook could be delivered here. Prints every request it receives,
answers 200 immediately (Bitrix retries on any non-200).

    python scripts/probe_listener.py            # 0.0.0.0:8000
    python scripts/probe_listener.py 8888       # custom port

Windows Firewall must allow inbound on the port (run once, as admin):
    New-NetFirewallRule -DisplayName "BitrixMCP probe" -Direction Inbound `
        -Protocol TCP -LocalPort 8000 -Action Allow
Remove it when the probe is done:
    Remove-NetFirewallRule -DisplayName "BitrixMCP probe"
"""
from __future__ import annotations

import socket
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Probe(BaseHTTPRequestHandler):
    def _log(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        print(f"\n[{datetime.now():%H:%M:%S}] {method} {self.path}  from {self.client_address[0]}")
        print(f"  content-type: {self.headers.get('Content-Type')}")
        if body:
            print(f"  body ({length} bytes): {body[:2000]}")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self) -> None:
        self._log("GET")

    def do_POST(self) -> None:
        self._log("POST")

    def log_message(self, *_args) -> None:
        pass  # replaced by _log above


def local_ips() -> list[str]:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        return sorted({i[4][0] for i in infos})
    except OSError:
        return []


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Listening on 0.0.0.0:{port} — waiting for a connection.")
    for ip in local_ips():
        print(f"  try from the Bitrix server:  curl -v http://{ip}:{port}/probe")
    print("Ctrl+C to stop.")
    ThreadingHTTPServer(("0.0.0.0", port), Probe).serve_forever()


if __name__ == "__main__":
    main()
