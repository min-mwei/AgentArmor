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


SESSION = "/session"  # the path prefix of a second, separately identified package (see main)


def handler(service, session=None):
    """One handler, one thread: requests are answered one at a time, whichever package they
    are for. session, when given, is a second Service with its own package identity, reached
    under the SESSION prefix. A caller chooses a path, never a model inside a request, and
    each path answers with its own package's identity for the caller to check."""
    resident = {"": service} | ({SESSION: session} if session is not None else {})

    def resolve(path, tail):
        for prefix, found in resident.items():
            if path == prefix + tail:
                return found
        return None

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
            found = resolve(self.path, "/health")
            if found is not None:
                self.reply(200, found.metadata())
            else:
                self.reply(404, DENIAL)

        def do_POST(self):
            try:
                found = resolve(self.path, "/v1/systemone")
                if found is None:
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
                result = found.evaluate(read_json(data))
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
    parser.add_argument(
        "--backend",
        choices=("vllm", "mlx"),
        default="vllm",
        help="hidden-state backbone: vllm (default) or mlx, the local Apple-silicon backend; "
        "MLX is qualified separately from vLLM",
    )
    parser.add_argument(
        "--session-package",
        help="a second local Kai export, answered under the /session path prefix with its own "
        "package identity; research only, with --package",
    )
    parser.add_argument(
        "--base-dir",
        help="for an unmerged package (schema agentarmor-qwen-lora/v1, MLX only): the directory "
        "of the registered base it is served beside; default, the local Hugging Face cache",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify the package(s), tokenizer and pointer, print their identities and exit "
        "without loading a weight or listening",
    )
    parser.add_argument("--port", type=int, default=8009)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    if not args.package and not args.kai_revision:
        parser.error("Kai requires either --kai-revision SHA or --package DIR")
    if args.kai_revision and not re.fullmatch(r"[0-9a-f]{40}", args.kai_revision):
        parser.error("--kai-revision must be a full 40-character HF commit")
    if args.session_package and not args.package:
        parser.error("--session-package goes with a local --package")
    if args.package and (args.cache_dir or args.local_files_only):
        parser.error("HF cache options do not apply to a local Kai package")
    if args.base_dir and not args.package:
        parser.error("--base-dir goes with a local --package")
    return args


def unmerged_options(args):
    """What only an unmerged package or a verification needs; nothing when neither was asked."""
    options = {"base_dir": args.base_dir} if args.base_dir else {}
    return options | ({"allocate": False} if args.verify_only else {})


def load_kai_service(args):
    from .engine import load_service

    if args.package:
        return load_service(args.package, backend=args.backend, **unmerged_options(args))
    from .cache import KAI_REPO, kai_snapshot

    package, blobs = kai_snapshot(args.kai_revision, args.cache_dir, args.local_files_only)
    return load_service(
        package,
        hf_blobs=blobs,
        source={"hf_repo": KAI_REPO, "revision": args.kai_revision},
        backend=args.backend,
        **unmerged_options(args),
    )


def main(argv=None):
    args = parse_args(argv)
    service = load_kai_service(args)
    session = None
    if args.session_package:
        from .engine import load_service

        session = load_service(args.session_package, backend=args.backend, **unmerged_options(args))
        if session.package.identity == service.package.identity:
            raise SystemExit("the session package is the default package: two identities needed")
    if args.verify_only:
        verified = {"default": service} | ({"session": session} if session else {})
        for role, found in verified.items():
            about = {
                "role": role,
                "schema": found.package.meta["schema"],
                "weights": found.package.meta["weights"],
                "package_sha256": found.package.identity,
                "verified": "package, tokenizer and pointer; no weight loaded",
            }
            if found.head_sha256 is not None:  # an evidence head: which, read by what
                about["evidence_head_sha256"] = found.head_sha256
                about["evidence"] = found.package.evidence
            print(json.dumps(about))
        return
    with HTTPServer(("127.0.0.1", args.port), handler(service, session)) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
