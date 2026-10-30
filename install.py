"""Bootstrap AgentArmor using only Python 3.10+ and its standard library.

python install.py --backend kai --kai-source /path/to/Kai
python install.py --backend cpu
"""

import argparse
import hashlib
import io
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

UV_VERSION = "0.13.4"
ROOT = Path(__file__).resolve().parent


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AgentArmor-installer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def choose_backend(requested, system=None, machine=None):
    system = system or platform.system()
    machine = machine or platform.machine()
    if requested == "auto":
        return "kai" if (system, machine) == ("Darwin", "arm64") else "cpu"
    if requested == "kai" and (system, machine) != ("Darwin", "arm64"):
        raise ValueError("The Kai MLX runtime requires Apple Silicon; use cpu for hook/API tests")
    if requested not in ("kai", "cpu"):
        raise ValueError("Choose kai or cpu; the previous vLLM integration is retired")
    return requested


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


def install_commands(uv, environment, root, backend, kai_source=None):
    python = environment / "bin" / "python"
    install = [str(uv), "pip", "install", "--python", str(python)]
    commands = [
        [str(uv), "venv", "--python", "3.13", "--allow-existing", str(environment)],
        [*install, "-e", str(root)],
    ]
    if backend == "kai":
        if kai_source is None or not (Path(kai_source) / "train/kai/decision.py").is_file():
            raise ValueError("Supply --kai-source pointing to the official Kai encoder repository")
        commands.append([*install, "-e", str(Path(kai_source).expanduser().resolve())])
    return commands


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("auto", "kai", "cpu"), default="auto")
    parser.add_argument("--kai-source", type=Path)
    parser.add_argument("--venv", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 10):  # noqa: UP036 - this bootstrap runs before package installation
        parser.error("Run the installer with Python 3.10 or newer")
    try:
        backend = choose_backend(args.backend)
    except ValueError as error:
        parser.error(str(error))
    environment = (args.venv or ROOT / ".agentarmor" / backend).expanduser().resolve()
    uv = ROOT / ".agentarmor" / "bootstrap" / "uv"
    try:
        commands = install_commands(uv, environment, ROOT, backend, args.kai_source)
    except ValueError as error:
        parser.error(str(error))
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
    # uv supplies a native 3.13 interpreter even when the entry Python is 3.10.
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
