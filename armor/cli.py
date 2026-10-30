"""Development and GPU serving entry point."""

import argparse
import json

from armor.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Kai decisions for coding-agent hooks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Start the HTTP service")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--backend", choices=("kai", "stub"))
    serve.add_argument("--model", help="served Kai model alias")
    serve.add_argument("--package", dest="candidate_package", help="registered Kai encoder package")
    subparsers.add_parser("inspect", help="Check checkpoint file names without downloading weights")
    subparsers.add_parser("doctor", help="Inspect installed agent clients and serving runtimes")
    subparsers.add_parser("config", help="Print effective server configuration (secrets redacted)")
    scan = subparsers.add_parser(
        "scan", help="Submit a UTF-8 file or chat log for background scanning"
    )
    scan.add_argument("path")
    scan.add_argument("--source", choices=("text_file", "chat_log"), default="text_file")
    scan.add_argument("--wait", action="store_true", help="Wait for the submitted job's result")
    args = parser.parse_args()
    overrides = {
        key: getattr(args, key)
        for key in ("host", "port", "backend", "model", "candidate_package")
        if getattr(args, key, None) is not None
    }
    settings = Settings(**overrides)
    if args.command == "doctor":
        from armor.runtime import diagnostics

        print(json.dumps(diagnostics(), indent=2))
    elif args.command == "scan":
        from armor.scan_client import read_text_file, submit_scan, wait_for_scan

        job = submit_scan(read_text_file(args.path), args.source, args.path)
        print(json.dumps(wait_for_scan(job["id"]) if args.wait else job, indent=2))
    elif args.command == "config":
        print(settings.model_dump_json(indent=2))
    elif args.command == "inspect":
        from armor.backend import inspect_model

        print(json.dumps(inspect_model(settings), indent=2))
    else:
        import uvicorn

        from armor.server import create_app

        # One HTTP worker owns one model engine. Scale with separate processes/devices.
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
