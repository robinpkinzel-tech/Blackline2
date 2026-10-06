"""Test-Attrappe für llama-server.

Versteht dieselben Kommandozeilenargumente (-m, --port, --api-key …) und
beantwortet /health sowie /v1/chat/completions. Als "KI" dient eine einfache
Heuristik: Nach "Herr/Frau/Herrn" folgende großgeschriebene Wörter gelten als Name.
"""

import argparse
import json
import re
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = re.compile(r"\b(?:Herrn?|Frau)\s+((?:Dr\.\s+)?[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?)")


def findings(text: str) -> list[dict]:
    out = []
    for m in NAME.finditer(text):
        name = m.group(1).replace("Dr. ", "")
        out.append({"text": name, "kategorie": "name", "bezug": name})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-m")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--api-key", default="")
    ap.add_argument("-c")
    ap.add_argument("-np")
    ap.add_argument("-ngl")
    ap.add_argument("-t")
    ap.add_argument("--jinja", action="store_true")
    args = ap.parse_args()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"status": "ok"})
            else:
                self._send(404, {})

        def do_POST(self):
            if self.headers.get("Authorization") != f"Bearer {args.api_key}":
                self._send(401, {"error": "unauthorized"})
                return
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            user = data["messages"][-1]["content"]
            page = user.split('"""')[1] if '"""' in user else user
            content = json.dumps({"funde": findings(page)})
            self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})

    class Server(ThreadingHTTPServer):
        def server_bind(self):  # ohne getfqdn(): das hängt auf manchen Rechnern ~30 s (DNS)
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "localhost", self.server_address[1]

    print("fake llama-server listening", flush=True)
    Server((args.host, args.port), H).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
