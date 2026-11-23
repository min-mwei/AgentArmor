"""Bound and scrub hook payloads before they reach disk. Standard library only.

The spool must not become a second copy of the user's credentials, so well-known secret formats are
replaced with [REDACTED:<kind>] before an event is written. Every clip and redaction is reported in
the event's metadata: a summary never claims to have seen text that was dropped here.
"""

import hashlib
import re

MAX_STRING = 262_144  # characters kept per string field
SCAN_LIMIT = 4_194_304  # larger strings are clipped before the redaction pass to bound its cost

_VALUE = r"(?=[A-Za-z0-9+/_=-]*\d)[A-Za-z0-9+/_=-]{16,}"
_PATTERNS = tuple(
    (kind, re.compile(pattern, flags))
    for kind, pattern, flags in (
        (
            "private_key",
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?"
            r"(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
            re.S,
        ),
        ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", 0),
        ("github_token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})", 0),
        ("anthropic_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}", 0),
        ("openai_key", r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}", 0),
        ("slack_token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}", 0),
        ("google_api_key", r"\bAIza[0-9A-Za-z_-]{35}", 0),
        ("huggingface_token", r"\bhf_[A-Za-z0-9]{30,}", 0),
        ("stripe_key", r"\b[sr]k_live_[A-Za-z0-9]{20,}", 0),
        ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", 0),
        ("bearer_token", r"(?P<keep>\bbearer\s+)[A-Za-z0-9._~+/=-]{20,}", re.I),
        (
            "assigned_secret",
            r"(?P<keep>\b[A-Za-z0-9_]*(?:secret|passwd|password|token|api_?key|access_?key)"
            r"[A-Za-z0-9_]*[\"']?\s*[:=]\s*[\"']?)" + _VALUE,
            re.I,
        ),
    )
)


def redact(text):
    """Return (text with secrets replaced, {kind: count})."""
    kinds = {}
    if len(text) < 20:
        return text, kinds
    for kind, pattern in _PATTERNS:

        def replace(match, kind=kind):
            kinds[kind] = kinds.get(kind, 0) + 1
            keep = match.groupdict().get("keep") or ""
            return f"{keep}[REDACTED:{kind}]"

        text = pattern.sub(replace, text)
    return text, kinds


def clip(text, limit=MAX_STRING):
    """Return (text within limit, note). The note records what was dropped; None if nothing was."""
    if len(text) <= limit:
        return text, None
    head = limit * 3 // 4
    tail = limit - head
    note = {
        "chars": len(text),
        "kept": limit,
        "sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
    }
    marker = f"\n[summarizer: {len(text) - limit} characters omitted]\n"
    return text[:head] + marker + text[-tail:], note


def compact(value, meta, path=""):
    """Copy a JSON value with every string redacted and clipped, recording each change in meta."""
    if isinstance(value, str):
        note = None
        if len(value) > SCAN_LIMIT:
            value, note = clip(value, SCAN_LIMIT)
        value, kinds = redact(value)
        value, later = clip(value)
        if note and later:  # report the original length, not the pre-clipped one
            later["chars"], later["sha256"] = note["chars"], note["sha256"]
        note = later or note
        if note:
            meta.setdefault("clipped", []).append({"path": path, **note})
        if kinds:
            meta.setdefault("redacted", []).append({"path": path, "kinds": kinds})
        return value
    if isinstance(value, dict):
        return {
            key: compact(item, meta, f"{path}.{key}" if path else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [compact(item, meta, f"{path}[{index}]") for index, item in enumerate(value)]
    return value
