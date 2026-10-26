"""Bounded candidate parity read. The CLI loads a model; comparison helpers are CPU-only."""

import argparse
import hashlib
import importlib.metadata
import json
import math
import statistics
import time
from pathlib import Path

from armor.candidate import load_contract, verify_snapshot
from armor.v0 import toolcall_score, xpia_flagged


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def compare(case, response, cutoffs, tolerance=0.001):
    """Require probability parity AND decision agreement, with valid diagnostic spans."""
    answers = response["answers"]
    expected = case["probabilities"]
    if set(answers) != set(expected) or set(answers) != set(case["request"]["questions"]):
        raise ValueError("Question IDs differ")
    if response.get("evidence_available") is not True or response.get("evidence_status") != (
        "diagnostic"
    ):
        raise ValueError("The candidate must return diagnostic evidence status")
    if response.get("usage", {}).get("output_tokens") != 0:
        raise ValueError("Kai must not generate output tokens")
    gap, span_count = 0.0, 0
    probabilities = {}
    state = case["request"]["state"]
    for qid, reference in expected.items():
        answer = answers[qid]
        p = answer["noul"]
        distribution = answer["probabilities"]
        if set(distribution) != {"false", "true"}:
            raise ValueError("Expected complete binary probabilities")
        values = [p, reference, *distribution.values()]
        if any(isinstance(x, bool) or not math.isfinite(x) or not 0 <= x <= 1 for x in values):
            raise ValueError("Invalid probability")
        if abs(sum(distribution.values()) - 1) > 1e-6 or distribution["true"] != p:
            raise ValueError("Inconsistent binary probabilities")
        gap = max(gap, abs(p - reference))
        probabilities[qid] = {"actual": p, "expected": reference, "abs_dp": abs(p - reference)}
        last = 0
        for span in answer["evidence_spans"]:
            if (
                len(span) != 2
                or any(type(x) is not int for x in span)
                or not 0 <= span[0] < span[1] <= len(state)
                or span[0] < last
                or not state[span[0] : span[1]].strip()
            ):
                raise ValueError("Invalid Unicode code-point evidence offset")
            span_count += 1
            last = span[1]
    if case.get("tiers") is not None:
        score, cutoff = toolcall_score(answers, case["tiers"], cutoffs)
        decision = "deny" if score > cutoff else "allow"
    else:
        decision = (
            "flagged" if xpia_flagged(answers["is_xpia_attempt"]["noul"], cutoffs) else "clean"
        )
    agrees = decision == case["decision"]
    return {
        "id": case["id"],
        "questions": len(answers),
        "max_abs_dp": gap,
        "probabilities": probabilities,
        "decision": decision,
        "expected_decision": case["decision"],
        "decision_agreed": agrees,
        "spans": span_count,
        "ok": gap <= tolerance and agrees,
    }


def timing_summary(values):
    ordered = sorted(values)
    return {
        "requests": len(values),
        "median_s": statistics.median(values),
        "p95_s": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "max_s": ordered[-1],
    }


def read(package, fixtures, out, repeats=20, max_model_len=8192):
    """Run the actual AgentArmor ASGI endpoint with its pinned local vLLM backend."""
    from fastapi.testclient import TestClient

    from armor.backend import VLLMBackend
    from armor.config import Settings
    from armor.server import create_app

    package, fixtures, out = Path(package).resolve(), Path(fixtures).resolve(), Path(out)
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite {out}")
    fixture = json.loads(fixtures.read_text())
    if fixture.get("schema") != "agentarmor-candidate-parity-v1" or not fixture["cases"]:
        raise ValueError("A nonempty registered fixture is required")
    if fixture["package_manifest_sha256"] != digest(package / "manifest.json"):
        raise ValueError("Fixture and package manifest differ")
    if repeats < 1:
        raise ValueError("Repeat count must be positive")
    exports = list((package / "hf").rglob("model.safetensors"))
    if len(exports) != 1:
        raise ValueError("Package must contain exactly one merged model under hf/")
    snapshot = exports[0].parent
    verify_snapshot(package, snapshot)
    cutoffs = load_contract(package)
    settings = Settings(
        _env_file=None,
        model=str(snapshot),
        revision=None,
        candidate_package=str(package),
        backend="vllm",
        served_model_name="kai",
        api_key=None,
        dtype="bfloat16",
        max_model_len=max_model_len,
        max_num_seqs=1,
        kv_cache_memory_mb=512,
        scan_profile="xpia-document-v0",
    )
    report = {
        "ok": False,
        "package": str(package),
        "manifest_sha256": digest(package / "manifest.json"),
        "fixtures_sha256": digest(fixtures),
        "reference_identity": fixture["reference_identity"],
        "runtime": {},
        "rows": [],
        "repeats": [],
        "latency": {},
        "probability_tolerance": 0.001,
        "span_scope": "Offset validity and diagnostic status only; no span-quality claim",
        "latency_scope": (
            "ASGI request through decision and span readout to JSON response; "
            "excludes network transport and model load"
        ),
    }
    for name in ("vllm", "vllm-metal", "mlx", "mlx-lm", "torch", "agentarmor"):
        try:
            report["runtime"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["runtime"][name] = None
    try:
        started = time.perf_counter()
        backend = VLLMBackend(settings)
        report["model_load_s"] = time.perf_counter() - started
        with TestClient(create_app(settings, backend=backend)) as client:
            for case in fixture["cases"]:
                started = time.perf_counter()
                response = client.post("/v1/systemone", json={**case["request"], "model": "kai"})
                elapsed = time.perf_counter() - started
                response.raise_for_status()
                row = compare(case, response.json(), cutoffs)
                report["rows"].append({**row, "seconds": elapsed})
            first_pass = {row["id"]: row for row in report["rows"]}
            for case in fixture["cases"]:
                if not case.get("latency_probe"):
                    continue
                durations = []
                for repeat in range(1, repeats + 1):
                    started = time.perf_counter()
                    response = client.post(
                        "/v1/systemone", json={**case["request"], "model": "kai"}
                    )
                    durations.append(time.perf_counter() - started)
                    response.raise_for_status()
                    row = compare(case, response.json(), cutoffs)
                    first = first_pass[case["id"]]
                    drift = max(
                        abs(p["actual"] - first["probabilities"][qid]["actual"])
                        for qid, p in row["probabilities"].items()
                    )
                    stable = (
                        drift <= report["probability_tolerance"]
                        and row["decision"] == first["decision"]
                    )
                    report["repeats"].append(
                        {
                            **row,
                            "repeat": repeat,
                            "seconds": durations[-1],
                            "max_abs_dp_from_first": drift,
                            "decision_agreed_with_first": row["decision"] == first["decision"],
                            "stable_against_first": stable,
                        }
                    )
                    report["latency"][case["id"]] = timing_summary(durations)
        report["ok"] = all(row["ok"] for row in report["rows"]) and all(
            row["ok"] and row["stable_against_first"] for row in report["repeats"]
        )
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["records"] = len(report["rows"])
        report["questions"] = sum(row["questions"] for row in report["rows"])
        report["max_abs_dp"] = max((row["max_abs_dp"] for row in report["rows"]), default=None)
        report["decision_disagreements"] = sum(not row["decision_agreed"] for row in report["rows"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--max-model-len", type=int, default=8192)
    args = parser.parse_args()
    result = read(args.package, args.fixtures, args.out, args.repeats, args.max_model_len)
    print(json.dumps({k: result[k] for k in ("ok", "records", "questions", "max_abs_dp")}))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
