"""Python reference for host-computed path facts (armor-hostfacts-paths-v1); CPU only, no model.

`src/hostfacts/paths.py` is byte-identical to Kai's `train/kai/hostfacts/paths.py`. The hand-written
vectors in `tests/fixtures/armor-hostfacts-paths-v1/` are shared with Kai.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/armor-hostfacts-paths-v1"
SOURCE = ROOT / "src/hostfacts/paths.py"
VECTORS = json.loads((FIXTURE / "vectors.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((FIXTURE / "MANIFEST.json").read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location("agentarmor_hostfacts_paths", SOURCE)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_manifest_pins_this_copy_and_the_fixture():
    # Kai holds the other copy. An edit to one copy without the other and the manifest fails here.
    assert sha(SOURCE) == MANIFEST["reference_python_sha256"]
    assert sha(FIXTURE / "vectors.json") == MANIFEST["vectors_sha256"]
    assert sha(FIXTURE / "make_vectors.py") == MANIFEST["builder_sha256"]
    assert len(VECTORS["cases"]) == MANIFEST["cases"]
    assert len(VECTORS["policy_cases"]) == MANIFEST["policy_cases"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["id"] for c in VECTORS["cases"]])
def test_hand_written_vector(case):
    out = P.compute_or_refusal(case["request"])
    expect = case["expect"]
    if expect["status"] == "refused":
        assert (out["status"], out.get("code")) == ("refused", expect["code"])
        return
    assert out["status"] == "ok", out
    assert [[f["name"], f["value"]] for f in out["facts"]] == expect["values"]
    if "facts" in expect:
        assert out["facts"] == expect["facts"]


@pytest.mark.parametrize(
    "case", VECTORS["policy_cases"], ids=[c["id"] for c in VECTORS["policy_cases"]]
)
def test_hand_written_policy_case(case):
    expect = case["expect"]
    if expect["status"] == "error":
        with pytest.raises(P.PolicyError) as exc:
            P.compile_policy(case["rules"], home=case["home"], workspace=case["workspace"])
        assert exc.value.code == expect["code"] and exc.value.hint
    else:
        compiled = P.compile_policy(case["rules"], home=case["home"], workspace=case["workspace"])
        assert compiled == expect["path_rules"]
