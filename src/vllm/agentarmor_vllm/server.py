"""Loopback diagnostic HTTP service; one resident engine, bounded serial work."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from .wire import MAX_BYTES, read_json

DENIAL = {
    "decision": "deny",
    "policy_ready": False,
    "coverage": "incomplete",
    "error": "AgentArmor could not complete the diagnostic check",
}


def handler(service):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass  # Never log prompts, URLs, or request bodies.

        def reply(self, status, body):
            data = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"backend": "vllm", "policy_ready": False, "diagnostic": True})
            else:
                self.reply(404, DENIAL)

        def do_POST(self):
            try:
                if self.path != "/v1/systemone":
                    self.reply(404, DENIAL)
                    return
                lengths = self.headers.get_all("Content-Length", [])
                if (
                    self.headers.get("Transfer-Encoding")
                    or len(lengths) != 1
                    or not lengths[0].isdigit()
                ):
                    raise ValueError("invalid framing")
                size = int(lengths[0])
                if not 0 < size <= MAX_BYTES:
                    self.reply(413, DENIAL)
                    return
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError("incomplete body")
                result = service.evaluate(read_json(data))
            except (ValueError, KeyError, TypeError, RecursionError, TimeoutError):
                self.reply(400, DENIAL)
                return
            except Exception:
                self.reply(503, DENIAL)
                return
            self.reply(200, result)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--port", type=int, default=8009)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    from .engine import load_service

    service = load_service(args.package)
    with HTTPServer(("127.0.0.1", args.port), handler(service)) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
