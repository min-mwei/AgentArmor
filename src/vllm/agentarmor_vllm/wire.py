"""Bounded JSON decoding shared by HTTP and package readers."""

import json
import math

MAX_BYTES = 1_000_000


def check_json(value, depth=0):
    if depth > 64:
        raise ValueError("JSON nesting exceeds 64")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite JSON value")
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON keys must be strings")
            key.encode("utf-8", errors="strict")
            check_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            check_json(item, depth + 1)
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ValueError("unsupported JSON value")


def read_json(data):
    if len(data) > MAX_BYTES:
        raise ValueError("request too large")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject(_):
        raise ValueError("nonfinite JSON value")

    result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject)

    check_json(result)
    return result
