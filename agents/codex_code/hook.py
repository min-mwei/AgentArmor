#!/usr/bin/env python3
"""Run directly from a cloned AgentArmor checkout; no server dependencies needed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from armor.hooks import main  # noqa: E402

if __name__ == "__main__":
    main("codex_code")
