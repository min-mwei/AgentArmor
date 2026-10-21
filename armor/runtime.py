"""Runtime and client diagnostics without loading a model or touching the GPU."""

import importlib.metadata
import importlib.util
import platform
import shutil
import sys


def diagnostics():
    packages = {}
    for name in ("agentarmor", "vllm", "vllm-metal", "mlx", "torch"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    metal = bool(importlib.util.find_spec("vllm_metal"))
    return {
        "python": sys.version.split()[0],
        "system": platform.system(),
        "machine": platform.machine(),
        "packages": packages,
        "agents": {name: shutil.which(name) for name in ("codex", "claude")},
        "runtime_installed": packages["vllm"] is not None,
        "runtime": "metal" if metal else "cuda" if platform.system() == "Linux" else None,
        "metal_adapter_version_supported": not metal
        or str(packages["vllm-metal"]).split("+", 1)[0] == "0.32.0",
        "model_loaded": False,
        "note": "Package/client discovery only; run a model smoke test to validate inference.",
    }
