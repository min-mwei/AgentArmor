"""Freeze 24 development requests plus package examples against saved Kai probabilities.

CPU only. Use the Kai Python environment. No model load, calibration or locked-test read.
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kai-source", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--reads", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    root, package = args.kai_source.resolve(), args.package.resolve()
    sys.path.insert(0, str(root))
    from kai.candidate import decide_from_answers, panel_tiers, request_sha256
    from kai.core.data import api_request
    from kai.core.suite import digest, read_json, read_jsonl, write_json

    manifest = read_json(package / "manifest.json")
    cutoffs = read_json(package / "cutoffs.json")
    assert digest(package / "cutoffs.json") == manifest["cutoffs"]["sha256"]
    # Match the existing package check's first 24 ToolCall process records.
    panel = root / "data/toolcall/eval/policy-generated-dev-v2/dev.jsonl"
    bindings = read_json(args.reads)["bindings"]
    matched = []
    for binding in bindings:
        if binding["panel_sha256"] != digest(panel):
            continue
        report_path = root / binding["report"]
        assert digest(report_path) == binding["report_sha256"]
        report = read_json(report_path)
        if report["identity"] == manifest["checkpoint"]["identity"]:
            matched.append(binding)
    if len(matched) != 1:
        raise ValueError("Need one saved development read from this checkpoint identity")
    binding = matched[0]
    assert digest(root / binding["rows"]) == binding["rows_sha256"]
    rows = read_json(root / binding["rows"])
    lookup = {(row["id"], row["question"]): row for row in rows}
    assert len(lookup) == len(rows)
    cases, sources = [], {"saved_read": binding, "reads_sha256": digest(args.reads)}
    for name in ("toolcall", "xpia"):
        request_path = package / "example" / f"{name}.request.json"
        response_path = package / "example" / f"{name}.response.json"
        request, response = read_json(request_path), read_json(response_path)
        assert response["request_sha256"] == request_sha256(request)
        # Saved package decisions expose only these binary probabilities. Question
        # branches are isolated, so omit auxiliary questions from this parity read.
        request = {
            **request,
            "questions": {qid: request["questions"][qid] for qid in response["probabilities"]},
        }
        tiers = read_json(package / "example/toolcall.tiers.json") if name == "toolcall" else None
        cases.append(
            {
                "id": f"example/{name}",
                "request": request,
                "tiers": tiers,
                "probabilities": response["probabilities"],
                "decision": response["decision"],
                "latency_probe": True,
            }
        )
        sources[name] = {
            "request_sha256": digest(request_path),
            "response_sha256": digest(response_path),
            "question_projection": "decision-driving questions from the package response",
        }
    records = [r for r in read_jsonl(panel) if panel_tiers(r) is not None][:24]
    if len(records) != 24:
        raise ValueError("The frozen request selection requires 24 process records")
    for record in records:
        rid = record["_meta"]["id"]
        request, tiers = api_request(record), panel_tiers(record)
        probabilities = {}
        for qid in request["questions"]:
            row = lookup[(rid, qid)]
            assert set(row["keys"]) == {"false", "true"}
            probabilities[qid] = row["p"][row["keys"].index("true")]
        answers = {qid: {"type": "noul", "noul": p} for qid, p in probabilities.items()}
        decision = decide_from_answers(request, answers, cutoffs, tiers)["decision"]
        cases.append(
            {
                "id": rid,
                "request": request,
                "tiers": tiers,
                "probabilities": probabilities,
                "decision": decision,
            }
        )
    result = {
        "schema": "agentarmor-candidate-parity-v1",
        "cases": cases,
        "package_manifest_sha256": digest(package / "manifest.json"),
        "reference_identity": manifest["checkpoint"]["identity"],
        "sources": sources,
    }
    write_json(args.out, result)
    print(
        json.dumps({"fixtures": str(args.out), "sha256": digest(args.out), "records": len(cases)})
    )


if __name__ == "__main__":
    main()
