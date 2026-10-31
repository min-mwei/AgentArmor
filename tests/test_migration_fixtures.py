"""Keep the retired renderer's byte/offset references intact for native adoption."""

import hashlib
import json


def test_migration_fixture_bytes_and_code_point_offsets(repo_root):
    bundle = json.loads((repo_root / "tests/fixtures/renderer_migration.json").read_text())
    for fixture in bundle["fixtures"]:
        state = fixture["expected_state"]
        assert hashlib.sha256(state.encode()).hexdigest() == fixture["expected_state_sha256"]
        for field, start, end, offset in fixture.get("expected_locations", []):
            assert 0 <= start < end <= len(state)
            assert state[start:end] == fixture["input"][field][offset : offset + end - start]
