import hashlib
import json

import pytest

from armor.candidate import load_contract, scan_cutoffs, verify_snapshot
from armor.v0 import toolcall_score


def package(tmp_path):
    cuts = {"standard": "v2.2", "toolcall": {"cutoff": 0.5}, "bipia": {"cutoff": -7.0}}
    data = json.dumps(cuts).encode()
    (tmp_path / "cutoffs.json").write_bytes(data)
    snapshot = tmp_path / "weights"
    snapshot.mkdir()
    pins = {}
    for name in (
        "model.safetensors",
        "config.json",
        "head.pt",
        "tokenizer.json",
        "tokenizer_config.json",
    ):
        value = ("synthetic fixture " + name).encode()
        (snapshot / name).write_bytes(value)
        pins[name] = hashlib.sha256(value).hexdigest()
    manifest = {
        "cutoffs": {"sha256": hashlib.sha256(data).hexdigest()},
        "serving": {"merged_bf16": {"files_sha256": pins}},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return snapshot


def test_candidate_cutoffs_and_model_have_to_match_the_manifest(tmp_path):
    snapshot = package(tmp_path)
    assert load_contract(tmp_path)["standard"] == "v2.2"
    verify_snapshot(tmp_path, snapshot)
    (snapshot / "head.pt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="file pins"):
        verify_snapshot(tmp_path, snapshot)
    (tmp_path / "cutoffs.json").write_text("{}")
    with pytest.raises(ValueError, match="pin"):
        load_contract(tmp_path)


def test_unpinned_evidence_sidecar_is_refused(tmp_path):
    snapshot = package(tmp_path)
    (snapshot / "evidence_head.safetensors").write_bytes(b"unbound")
    with pytest.raises(ValueError, match="file pins"):
        verify_snapshot(tmp_path, snapshot)


def test_candidate_policy_standard_keeps_user_boundary_above_allow():
    answers = {
        qid: {"noul": p}
        for qid, p in {
            "rule_scratch_matches": 0.99,
            "active_user_boundary": 0.99,
            "user_requested_exact_operation": 0.01,
        }.items()
    }
    tiers = {"rule_scratch_matches": "allow"}
    cuts = {"toolcall": {"cutoff": 0.5}}
    assert toolcall_score(answers, tiers, {**cuts, "standard": "v2.1"})[0] < 0.5
    assert toolcall_score(answers, tiers, {**cuts, "standard": "v2.2"})[0] > 0.5


def test_contextual_profile_cannot_reuse_a_document_cutoff():
    cuts = {"bipia": {"cutoff": -7.0}}
    with pytest.raises(ValueError, match="registered"):
        scan_cutoffs(cuts, "xpia-context-v1")
    cuts["xpia_context"] = {"profile": "xpia-context-v1", "cutoff": -2.0}
    assert scan_cutoffs(cuts, "xpia-context-v1")["bipia"]["cutoff"] == -2.0
    cuts["xpia_context"]["cutoff"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        scan_cutoffs(cuts, "xpia-context-v1")


def test_retired_model_runtimes_are_not_selectable():
    from armor.config import Settings

    for backend in ("vllm", "reference"):
        with pytest.raises(ValueError):
            Settings(_env_file=None, backend=backend)


def test_guard_uses_the_packages_standard_and_cutoff(tmp_path, guard_body):
    from fastapi.testclient import TestClient

    from armor.config import Settings
    from armor.server import create_app

    package(tmp_path)
    guard_body["policy"] = json.dumps(
        {"rules": [{"id": "scratch", "tier": "allow", "description": "Allow scratch writes."}]}
    )

    class Backend:
        def decide(self, request):
            return {
                "answers": {
                    q: {"noul": 0.99 if q != "user_requested_exact_operation" else 0.0}
                    for q in request.questions
                },
                "evidence_available": False,
            }

    settings = Settings(_env_file=None, backend="stub", candidate_package=str(tmp_path))
    with TestClient(create_app(settings, Backend())) as client:
        response = client.post("/v1/guard", json=guard_body)
        assert response.status_code == 200
        assert response.json()["decision"] == "deny"
        assert response.json()["cutoff"] == 0.5
        assert response.json()["evidence_status"] == "unavailable"
