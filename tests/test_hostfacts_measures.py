"""Python reference for quantity and time host facts (armor-hostfacts-measures-v1); CPU only.

`src/hostfacts/measures.py` is byte-identical to Kai's `train/kai/hostfacts/measures.py`. The
hand-written vectors in `tests/fixtures/armor-hostfacts-measures-v1/` are shared with Kai and with
the serving integration.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/armor-hostfacts-measures-v1"
SOURCE = ROOT / "src/hostfacts/measures.py"
VECTORS = json.loads((FIXTURE / "vectors.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((FIXTURE / "MANIFEST.json").read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location("agentarmor_hostfacts_measures", SOURCE)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_manifest_pins_this_copy_and_the_fixture():
    assert sha(SOURCE) == MANIFEST["reference_python_sha256"]
    assert sha(FIXTURE / "vectors.json") == MANIFEST["vectors_sha256"]
    assert sha(FIXTURE / "make_vectors.py") == MANIFEST["builder_sha256"]
    assert len(VECTORS["cases"]) == MANIFEST["cases"]
    assert len(VECTORS["policy_cases"]) == MANIFEST["policy_cases"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["id"] for c in VECTORS["cases"]])
def test_hand_written_vector(case):
    out = M.compute_or_refusal(case["request"])
    expect = case["expect"]
    if expect["status"] == "refused":
        assert (out["status"], out.get("code")) == ("refused", expect["code"])
        return
    assert out["status"] == "ok", out
    assert [[f["name"], f["value"]] for f in out["facts"]] == expect["values"]


@pytest.mark.parametrize(
    "case", VECTORS["policy_cases"], ids=[c["id"] for c in VECTORS["policy_cases"]]
)
def test_hand_written_policy_case(case):
    expect = case["expect"]
    if expect["status"] == "error":
        with pytest.raises(M.PolicyError) as exc:
            M.compile_limits(case["rules"], config=case["config"])
        assert exc.value.code == expect["code"] and exc.value.hint
    else:
        assert M.compile_limits(case["rules"], config=case["config"]) == expect["limit_rules"]


def test_key_names_in_refusal_details_always_encode():
    # A lone surrogate in a key name is escaped, so the detail encodes as UTF-8 (and is ASCII).
    request = json.loads(json.dumps(VECTORS["cases"][0]["request"]))
    request["\ud800"] = 1
    out = M.compute_or_refusal(request)
    assert (out["status"], out["code"]) == ("refused", "bad_request")
    assert out["detail"] == 'request has unknown keys "\\ud800"'
    assert json.dumps(out, ensure_ascii=False).encode("utf-8")
