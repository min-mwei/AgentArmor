"""Runtime and client diagnostics without loading a model or touching the GPU."""

import importlib.metadata
import importlib.util
import platform
import shutil
import sys


def diagnostics():
    packages = {}
    for name in ("agentarmor", "kai", "mlx", "mlx-lm"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": sys.version.split()[0],
        "system": platform.system(),
        "machine": platform.machine(),
        "packages": packages,
        "agents": {name: shutil.which(name) for name in ("codex", "claude")},
        "runtime_installed": packages["kai"] is not None and packages["mlx"] is not None,
        "runtime": "mlx"
        if platform.system() == "Darwin" and platform.machine() == "arm64"
        else None,
        "model_loaded": False,
        "note": "Package/client discovery only; run a model smoke test to validate inference.",
    }
