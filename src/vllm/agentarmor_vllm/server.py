"""Serve Kai-4B typed decision probabilities through a loopback HTTP service."""

import argparse
import json
import re
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
                self.reply(200, service.metadata())
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
                # Validate response serialization before sending any success headers.
                json.dumps(result, allow_nan=False)
            except (ValueError, KeyError, TypeError, RecursionError, TimeoutError):
                self.reply(400, DENIAL)
                return
            except Exception:
                self.reply(503, DENIAL)
                return
            self.reply(200, result)

    return Handler


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("kai-4b",), default="kai-4b", help="Kai-4B only")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--package", help="local Kai vLLM export directory")
    source.add_argument(
        "--kai-revision", help="full commit in min-h-wei/Kai-4B with a vllm/ export"
    )
    parser.add_argument("--local-files-only", action="store_true", help="disable Kai HF downloads")
    parser.add_argument("--cache-dir", help="Hugging Face hub cache directory")
    parser.add_argument("--port", type=int, default=8009)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    if not args.package and not args.kai_revision:
        parser.error("Kai requires either --kai-revision SHA or --package DIR")
    if args.kai_revision and not re.fullmatch(r"[0-9a-f]{40}", args.kai_revision):
        parser.error("--kai-revision must be a full 40-character HF commit")
    if args.package and (args.cache_dir or args.local_files_only):
        parser.error("HF cache options do not apply to a local Kai package")
    return args


def load_kai_service(args):
    from .engine import load_service

    if args.package:
        return load_service(args.package)
    from .cache import KAI_REPO, kai_snapshot

    package, blobs = kai_snapshot(args.kai_revision, args.cache_dir, args.local_files_only)
    return load_service(
        package, hf_blobs=blobs, source={"hf_repo": KAI_REPO, "revision": args.kai_revision}
    )


def main(argv=None):
    args = parse_args(argv)
    service = load_kai_service(args)
    with HTTPServer(("127.0.0.1", args.port), handler(service)) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
