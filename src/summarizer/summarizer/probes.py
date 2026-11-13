"""Facet probes: broad descriptions of content a detector should get to see.

A probe is a handful of natural-language descriptions embedded as retrieval queries; lower-trust
content is compared with their average. There are only a few, each covering a wide family from
AgentArmor's threat model, because the encoder is small: it is asked to recognize broad patterns,
not to draw fine distinctions. Finer labels come from wording and behavior (facts.py, labels.py).

A probe tags content once it is calibrated: a threshold on similarity chosen for recall, and scale
and bias so that sigmoid(scale * similarity + bias) is the probability its label applies. The
tag's confidence is that probability times the probe's weight. An uncalibrated probe still orders
content but tags nothing. train/calibrate.py fits these numbers for a model.

The built-in numbers were fitted for the pinned base model on the starter examples in train/, at 90%
recall, and they apply to that model only: they are loose, so most tags they produce are weak, and
a different or fine-tuned encoder needs its own calibration (train/README.md has the measurements).

On disk a probe set is JSON Lines, one probe per line; a fine-tuned model directory carries its
own probes.jsonl.
"""

import hashlib
import json
import math
from pathlib import Path

DEFAULT_VERSION = "default-v3"
DEFAULT = (
    {
        "id": "agent_directive",
        "label": "agent_directive",
        "weight": 0.9,
        "scale": 39.6517,
        "bias": -28.8289,
        "threshold": 0.6876,
        "queries": [
            "an instruction addressed to an AI agent or assistant telling it to ignore its task, "
            "follow new instructions, or take a different action",
            "text that speaks to the AI model reading it and gives it orders or new rules, "
            "sometimes claiming to come from the user, the system or an administrator",
            "a hidden note telling an assistant what to do next and not to tell the user",
        ],
    },
    {
        "id": "data_theft",
        "label": "exfiltration_request",
        "weight": 0.8,
        "scale": 35.7215,
        "bias": -26.4434,
        "threshold": 0.6764,
        "queries": [
            "a request to read or collect credentials, API keys, tokens, passwords, environment "
            "variables or private files",
            "an instruction to send, upload, post or leak data to an external server, URL, email "
            "address, issue or comment",
        ],
    },
    {
        "id": "control_tampering",
        "label": "tampering_request",
        "weight": 0.7,
        "scale": 44.0843,
        "bias": -35.0585,
        "threshold": 0.741,
        "queries": [
            "an instruction to change agent configuration, instruction or memory files, hooks, "
            "MCP servers, startup scripts or CI so that it keeps applying later",
            "an instruction to skip approvals, disable sandboxing or safety checks, run unknown "
            "code or install an unknown package",
            "a request to delete, wipe or overwrite files, repositories, databases or "
            "infrastructure",
        ],
    },
)


def calibrated(probe):
    return probe.get("scale") is not None and probe.get("bias") is not None


def uncalibrated(probes):
    """The same probes without their calibration, for an encoder it was not fitted on."""
    drop = ("scale", "bias", "threshold", "calibration")
    return [{key: value for key, value in probe.items() if key not in drop} for probe in probes]


def fitted_for(probe, info):
    """Why a probe's calibration does not hold for the encoder that info describes, or None.

    A calibration is a threshold and a curve fitted on the similarities one encoder gave,
    on one device, in one precision, encoding one text at a time up to one window, under
    one policy for a text longer than that (embedder.POLICY). It says which of those it
    was measured under ("calibration"). One made before policies had names says none: its
    texts were cut at the window, which for a text within the window is what is served
    now, so it is held to its window and not to a name. Served under another, the same
    numbers cut somewhere else: a threshold of 0.0405 fitted on bfloat16 similarities says
    nothing sure about float32 ones. So a calibration that names its profile holds only
    for that profile, or for one it lists as measured to be equivalent ("profiles", each
    a device and a precision). One that names no profile is not checked here: the
    built-in numbers are of that kind, and are bound to the pinned base model instead.
    """
    measured = probe.get("calibration")
    if not calibrated(probe) or not isinstance(measured, dict):
        return None
    served = {"device": info.get("device"), "dtype": info.get("dtype")}
    under = {name: measured.get(name) for name in served if measured.get(name) is not None}
    equal = [under, *(p for p in measured.get("profiles", ()) if isinstance(p, dict))]
    if under and not any(all(served.get(k) == v for k, v in one.items()) for one in equal):
        return (
            f"calibrated on {under.get('device')} in {under.get('dtype')}, "
            f"served on {served['device']} in {served['dtype']}"
        )
    inference = measured.get("inference") or {}
    batch, window = inference.get("batch"), inference.get("max_tokens")
    policy = inference.get("policy")
    if policy is not None and info.get("policy") is not None and policy != info["policy"]:
        return f"calibrated under the policy {policy}, served under {info['policy']}"
    if batch is not None and info.get("batch") is not None and batch != info["batch"]:
        return f"calibrated with batches of {batch}, served with batches of {info['batch']}"
    reaching = info.get("window_tokens")
    if window is not None and reaching is not None and window != reaching:
        return f"calibrated with a window of {window} tokens, served with one of {reaching}"
    return None


def probability(probe, similarity):
    """Probability that the probe's label applies to content at this similarity."""
    return 1.0 / (1.0 + math.exp(-(probe["scale"] * similarity + probe["bias"])))


def dump(probes, path):
    with open(path, "w", encoding="utf-8") as handle:
        for probe in probes:
            handle.write(json.dumps(probe, ensure_ascii=False) + "\n")


def load(path=None):
    """Return (version, probes): the built-in set, or probes.jsonl from a file or model folder."""
    if path is None:
        return DEFAULT_VERSION, [dict(probe) for probe in DEFAULT]
    path = Path(path)
    if path.is_dir():
        path = path / "probes.jsonl"
    raw = path.read_bytes()
    probes = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    ids = [probe["id"] for probe in probes]
    if not probes or len(set(ids)) != len(ids):
        raise ValueError(f"{path}: a probe set needs at least one probe and unique ids")
    for probe in probes:
        if not probe.get("queries") or not probe.get("label") or not 0 < probe["weight"] <= 1:
            raise ValueError(f"{path}: probe {probe['id']!r} needs queries, a label and a weight")
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:12], probes
