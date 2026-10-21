"""Bootstrap AgentArmor using only Python 3.10+ and its standard library.

python install.py --backend metal|cuda|cpu
"""

import argparse
import hashlib
import io
import json
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

CUDA_VERSION = "0.32.0"
METAL_VERSION = "0.32.0"
UV_VERSION = "0.13.4"
ROOT = Path(__file__).resolve().parent


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AgentArmor-installer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def metal_wheels(release=METAL_VERSION):
    data = fetch_json(
        f"https://api.github.com/repos/vllm-project/vllm-metal/releases/tags/v{release}"
    )
    wheels = [
        item["browser_download_url"]
        for item in data["assets"]
        if item["name"].endswith("-cp312-cp312-macosx_15_0_arm64.whl")
    ]
    if len(wheels) != 1:
        raise RuntimeError("Expected one prebuilt Python 3.12 Apple Silicon Metal wheel")
    metadata_url = (
        f"https://raw.githubusercontent.com/vllm-project/vllm-metal/v{release}/"
        ".github/vllm-release-tag.commit"
    )
    with urllib.request.urlopen(metadata_url, timeout=30) as response:
        tag = response.read().decode().strip()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise RuntimeError("Metal release has invalid vLLM compatibility metadata")
    version = tag[1:]
    core = (
        f"https://github.com/vllm-project/vllm/releases/download/{tag}/"
        f"vllm-{version}%2Bcpu-cp38-abi3-macosx_11_0_arm64.whl"
    )
    return [core, wheels[0]]


def choose_backend(requested, system=None, machine=None):
    system = system or platform.system()
    machine = machine or platform.machine()
    backend = requested
    if backend == "auto":
        if (system, machine) == ("Darwin", "arm64"):
            backend = "metal"
        elif system == "Linux":
            backend = "cuda"
        else:
            raise ValueError("Use Apple Silicon/macOS for Metal or Linux for CUDA")
    if system not in ("Darwin", "Linux"):
        raise ValueError("This installer supports macOS and Linux; use WSL on Windows")
    if backend == "metal" and (system, machine) != ("Darwin", "arm64"):
        raise ValueError(
            "Metal requires an Apple Silicon Mac; use cuda on Linux or cpu for hook tests"
        )
    if backend == "cuda" and system != "Linux":
        raise ValueError("CUDA vLLM requires Linux; use metal on Apple Silicon")
    return backend


def bootstrap_uv(destination):
    """Extract the verified standalone uv binary; pip, Git and a compiler are unnecessary."""
    system, machine = platform.system(), platform.machine()
    tags = {
        ("Darwin", "arm64"): "macosx_11_0_arm64",
        ("Darwin", "x86_64"): "macosx_10_12_x86_64",
        ("Linux", "x86_64"): "manylinux_2_17_x86_64.manylinux2014_x86_64",
        ("Linux", "aarch64"): "manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64",
    }
    if (system, machine) not in tags:
        raise ValueError(f"No bootstrap wheel configured for {system}/{machine}")
    filename = f"uv-{UV_VERSION}-py3-none-{tags[system, machine]}.whl"
    metadata = fetch_json(f"https://pypi.org/pypi/uv/{UV_VERSION}/json")
    asset = next(item for item in metadata["urls"] if item["filename"] == filename)
    with urllib.request.urlopen(asset["url"], timeout=60) as response:
        wheel = response.read()
    if hashlib.sha256(wheel).hexdigest() != asset["digests"]["sha256"]:
        raise RuntimeError("uv bootstrap wheel checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        executable = archive.read(f"uv-{UV_VERSION}.data/scripts/uv")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(executable)
    destination.chmod(0o755)


def install_commands(uv, environment, root, backend, wheels=()):
    python = environment / "bin" / "python"
    create = [str(uv), "venv", "--python", "3.12", "--allow-existing", str(environment)]
    install = [str(uv), "pip", "install", "--python", str(python)]
    commands = [create]
    if backend == "cuda":
        commands.append([*install, f"vllm=={CUDA_VERSION}"])
    elif backend == "metal":
        if len(wheels) != 2:
            raise ValueError("Metal needs its paired vLLM core and plugin wheels")
        commands.append(
            [*install, "--override", str(root / "armor/resources/metal-overrides.txt"), *wheels]
        )
    # Install the API separately: a Linux PyPI vLLM wheel must not replace Metal's CPU core.
    commands.append([*install, "-e", str(root)])
    return commands


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("auto", "metal", "cuda", "cpu"), default="auto")
    parser.add_argument("--venv", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 10):  # noqa: UP036 - this bootstrap runs before package installation
        parser.error("Run the installer with Python 3.10 or newer")
    try:
        backend = choose_backend(args.backend)
    except ValueError as error:
        parser.error(str(error))
    if backend == "metal" and int(platform.mac_ver()[0].split(".")[0]) < 15:
        parser.error("The prebuilt Metal wheels require macOS 15 or later")
    environment = (args.venv or ROOT / ".agentarmor" / backend).expanduser().resolve()
    uv = ROOT / ".agentarmor" / "bootstrap" / "uv"
    wheels = metal_wheels() if backend == "metal" else ()
    commands = install_commands(uv, environment, ROOT, backend, wheels)
    if args.dry_run:
        print(
            json.dumps(
                {"backend": backend, "environment": str(environment), "commands": commands},
                indent=2,
            )
        )
        return
    if not uv.is_file():
        bootstrap_uv(uv)
    # uv supplies a native 3.12 interpreter even when the entry Python is 3.10.
    for command in commands:
        subprocess.run(command, check=True)
    print(
        json.dumps(
            {
                "backend": backend,
                "python": str(environment / "bin" / "python"),
                "agents": {name: shutil.which(name) for name in ("codex", "claude")},
                "next": [str(environment / "bin" / "agentarmor"), "doctor"],
                "note": "Installed; doctor checks package discovery, not model inference.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
