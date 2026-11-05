"""Weight-free contract tests. These do not establish vLLM/MLX model parity."""

import http.client
import json
import math
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from types import SimpleNamespace

import numpy as np
import pytest
from agentarmor_vllm.encode import SPECIAL, tokenizer_sha256
from agentarmor_vllm.engine import Pointer, Service
from agentarmor_vllm.server import handler
from agentarmor_vllm.wire import read_json


class Tokenizer:
    unk_token_id = -1
    backend_tokenizer = SimpleNamespace(to_str=lambda: "synthetic-tokenizer/v1")

    def convert_tokens_to_ids(self, text):
        return SPECIAL.index(text) + 1

    def __call__(self, text, **_):
        return SimpleNamespace(input_ids=[ord(c) + 10 for c in text])


class Backbone:
    def __init__(self):
        self.rows = []

    def hidden(self, row):
        self.rows.append(row)
        return np.asarray([[i, 1] for i in range(len(row["ids"]))], dtype=np.float32)


def service():
    tok = Tokenizer()
    package = SimpleNamespace(
        meta={
            "tokenizer_sha256": tokenizer_sha256(tok),
            "pauses": 0,
            "pause_id": None,
            "max_state": 384,
            "max_row": 1024,
            "max_packed": 2048,
            "temperature": 2.0,
        },
        identity="synthetic",
    )
    pointer = Pointer({"q.weight": [[0, 0]], "q.bias": [2], "k.weight": [[1, 0]], "k.bias": [1]}, 2)
    return Service(package, tok, pointer, Backbone())


def request():
    return {
        "state": "α <|fim_suffix|>",
        "questions": {
            "one": {"type": "choice", "instructions": "pick", "criteria": {"z": "Z", "a": "A"}},
            "two": {"type": "noul", "instructions": "check"},
        },
    }


def test_canonical_rows_escaping_pointer_temperature():
    s = service()
    result = s.evaluate(request())
    assert result["policy_ready"] is False and result["evidence_available"] is False
    assert list(result["answers"]["one"]["probabilities"]) == ["a", "z"]
    a, b = s.backbone.rows
    # Independent state prefixes; neither row contains its sibling question.
    assert a["ids"].count(2) == b["ids"].count(2) == 1
    assert a["ids"].count(5) == b["ids"].count(5) == 1
    assert a["ids"][: a["ids"].index(2)] == b["ids"][: b["ids"].index(2)]
    assert [ord(c) + 10 for c in "<¦fim_suffix¦>"] == a["ids"][3 : a["ids"].index(2)]
    # q=2, k=option_end+1; temperature=2 cancels q, leaving position difference.
    expected = 1 / (1 + math.exp(a["opts"][1] - a["opts"][0]))
    assert result["answers"]["one"]["probabilities"]["a"] == pytest.approx(expected)


@pytest.mark.parametrize("kind", ["state", "branch", "packed", "last_question"])
def test_overflow_refuses_before_any_forward(kind):
    s = service()
    req = request()
    if kind == "state":
        req["state"] = "x" * 384
    elif kind == "branch":
        req["questions"]["one"]["instructions"] = "x" * 1024
    elif kind == "last_question":
        req["questions"]["two"]["instructions"] = "x" * 1024
    else:
        s.package.meta["max_packed"] = 20
    with pytest.raises(ValueError):
        s.evaluate(req)
    assert not s.backbone.rows


def test_partial_or_nonfinite_backbone_never_returns_answers():
    for hidden in (np.zeros((1, 2)), np.full((40, 2), np.nan)):
        s = service()
        s.backbone.hidden = lambda _, h=hidden: h
        with pytest.raises(ValueError):
            s.evaluate(request())


def test_pointer_bias_and_scaling():
    p = Pointer(
        {
            "q.weight": [[1, 0], [0, 1]],
            "q.bias": [1, 2],
            "k.weight": [[2, 0], [0, 3]],
            "k.bias": [4, 5],
        },
        2,
    )
    actual = p.logits([[1, 2], [3, 4]], {"ids": [1, 2], "decide": 1, "opts": [0]})
    assert actual[0] == pytest.approx((6 * 4 + 11 * 6) / math.sqrt(2))


@pytest.mark.parametrize(
    "data",
    [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'"\xff"', b"[" * 66 + b"0" + b"]" * 66],
)
def test_strict_wire(data):
    with pytest.raises((ValueError, UnicodeError)):
        read_json(data)


def test_http_failures_deny_and_do_not_echo_payload():
    s = service()
    with HTTPServer(("127.0.0.1", 0), handler(s)) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/v1/systemone"
            data = json.dumps(request()).encode()
            with urllib.request.urlopen(
                urllib.request.Request(url, data=data), timeout=3
            ) as response:
                assert json.load(response)["coverage"] == "complete"
            for data in (b'{"secret":"SYNTHETIC",',):
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=3)
                body = error.value.read()
                assert json.loads(body)["decision"] == "deny"
                assert b"SYNTHETIC" not in body
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.putrequest("POST", "/v1/systemone")
            connection.putheader("Content-Length", "1000001")
            connection.endheaders()
            response = connection.getresponse()
            assert response.status == 413
            assert json.loads(response.read())["decision"] == "deny"
            connection.close()
            s.backbone.hidden = lambda _: (_ for _ in ()).throw(RuntimeError("SYNTHETIC"))
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(
                    urllib.request.Request(url, data=json.dumps(request()).encode()), timeout=3
                )
            assert error.value.code == 503
            assert json.loads(error.value.read())["coverage"] == "incomplete"
        finally:
            server.shutdown()
            thread.join()


def test_package_hashes_and_context(tmp_path):
    from agentarmor_vllm.package import BASE_REVISION, Package, sha256

    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text(
        json.dumps({"model_type": "qwen3_5_text", "hidden_size": 2560, "num_hidden_layers": 32})
    )
    (model / "tokenizer.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"synthetic weights, not loadable")
    (tmp_path / "pointer.safetensors").write_bytes(b"synthetic head, not loadable")
    meta = {
        "schema": "agentarmor-qwen/v1",
        "base": "Qwen/Qwen3.5-4B-Base",
        "base_revision": BASE_REVISION,
        "weights": "merged",
        "dtype": "bfloat16",
        "evidence": "frozen-from-pointer",
        "temperature": 1.0,
        "max_state": 384,
        "max_row": 1024,
        "max_packed": 2048,
        "pauses": 0,
        "pause_id": None,
        "tokenizer_sha256": "synthetic",
        "files": {
            p.relative_to(tmp_path).as_posix(): sha256(p)
            for p in tmp_path.rglob("*")
            if p.is_file()
        },
    }
    (tmp_path / "kai.json").write_text(json.dumps(meta))
    Package(tmp_path)
    (model / "model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        Package(tmp_path)
    meta["temperature"] = 0
    (tmp_path / "kai.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="temperature"):
        Package(tmp_path)


def test_trainer_snapshot_encoding_goldens(repo_root):
    from agentarmor_vllm.api import SystemOneRequest, to_record
    from agentarmor_vllm.encode import canonical, encode, rows_of

    fixture = json.loads((repo_root / "tests/fixtures/qwen_encoding.json").read_text())
    rec, meta = to_record(SystemOneRequest.model_validate(canonical(fixture["request"])))
    assert rec == fixture["record"] and meta == fixture["meta"]
    for case in fixture["cases"]:
        encoded = encode(Tokenizer(), rec, pauses=case["pauses"])
        assert encoded == case["encoded"]
        assert list(rows_of(encoded)) == case["rows"]
