"""Run tiny, weight-free Metal contracts only when Kai's device lock is free."""

import argparse
import fcntl
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("build", type=Path)
args = parser.parse_args()
lock = Path.home() / ".cache/kai/gpu.lock"
lock.parent.mkdir(parents=True, exist_ok=True)
with lock.open("a+") as stream:
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Kai device lock is occupied; Metal contracts were not run") from None
    raise SystemExit(
        subprocess.run(
            ["ctest", "--test-dir", str(args.build), "--output-on-failure", "-L", "gpu"],
            check=False,
        ).returncode
    )
