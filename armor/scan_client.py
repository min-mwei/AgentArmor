"""Small standard-library HTTP client, usable from agent hook subprocesses."""

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

MAX_BYTES = 1_000_000


def operator_context() -> dict:
    policy_file = os.environ.get("AGENTARMOR_POLICY_FILE")
    task = os.environ.get("AGENTARMOR_TASK", "").strip()
    if not policy_file or not task:
        raise ValueError("Set AGENTARMOR_POLICY_FILE and AGENTARMOR_TASK")
    policy = Path(policy_file).expanduser().read_text(encoding="utf-8")
    if not policy.strip():
        raise ValueError("The operator policy is empty")
    return {"policy": policy, "task": task}


def request_json(path: str, payload: dict | None = None, timeout: float = 20) -> dict:
    base = os.environ.get("AGENTARMOR_URL", "http://127.0.0.1:8000").rstrip("/")
    if urllib.parse.urlsplit(base).scheme not in ("http", "https"):
        raise ValueError("AGENTARMOR_URL must be an HTTP(S) URL")
    headers = {"Content-Type": "application/json"}
    if key := os.environ.get("AGENTARMOR_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=headers,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Server response is too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Server response must be a JSON object")
    return result


def read_text_file(path: str | Path) -> str:
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise ValueError("Scan source must be an existing regular file")
    with file_path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Input exceeds the 1 MB scan limit; split it before submission")
    # Invalid UTF-8 or oversized input is refused, never silently truncated or replaced.
    return raw.decode("utf-8")


def submit_scan(text: str, source: str, source_name: str) -> dict:
    return request_json(
        "/v1/scans",
        {
            "text": text,
            "source": source,
            "source_name": source_name,
            **operator_context(),
        },
    )


def wait_for_scan(job_id: str, timeout: float = 90) -> dict:
    if not job_id.startswith("scan-") or not job_id[5:].isalnum():
        raise ValueError("Invalid scan ID")
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Scan {job_id} is still pending; poll its status endpoint")
        job = request_json(f"/v1/scans/{job_id}", timeout=min(20, remaining))
        if job["status"] == "completed":
            return job["result"]
        if job["status"] == "failed":
            raise RuntimeError("Background scan failed; no clear verdict is available")
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
