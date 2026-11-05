"""Bounded JSON decoding shared by HTTP and package readers."""

import json
import math

MAX_BYTES = 1_000_000


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

    def check(value, depth=0):
        if depth > 64:
            raise ValueError("JSON nesting exceeds 64")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite JSON value")
        if isinstance(value, dict):
            for item in value.values():
                check(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                check(item, depth + 1)

    check(result)
    return result
