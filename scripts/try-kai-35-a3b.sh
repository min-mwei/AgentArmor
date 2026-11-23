#!/usr/bin/env bash
# Try Kai-35-A3B on Apple silicon with one command.
#
#     scripts/try-kai-35-a3b.sh                 # install, download, verify, start the Kai service
#     scripts/try-kai-35-a3b.sh --with-monitor  # the same, and the local monitor in front of it
#     scripts/try-kai-35-a3b.sh --download-only # stop after the downloads
#     scripts/try-kai-35-a3b.sh --verify-only   # stop after the package and base checks
#
# What it does, in order:
#   1. checks the machine (Apple silicon, Python 3.11 to 3.13, free disk, memory);
#   2. creates .venv-kai (or uses $VENV) and installs AgentArmor's MLX serving package;
#   3. downloads the Kai-35-A3B package (about 180 MB) at a pinned revision into $KAI_DIR, and the
#      base model Qwen3.6-35B-A3B (about 72 GB) at the revision the package names into the Hugging
#      Face cache; files already present are not downloaded again;
#   4. verifies every package and base file against the hashes in the package's kai.json;
#   5. starts the Kai service on 127.0.0.1:$PORT and prints example requests
#      (examples/kai-35-a3b/); with --with-monitor it also installs Summarizer and the monitor
#      in a second environment (their pins differ from the MLX serving pins) and starts the
#      monitor against that service, in the one-question contract.
#
# Nothing is enforced: the service returns scores and the monitor records them (policy_ready is
# false). About 25 GB of GPU memory is used while the model is loaded; 64 GB is recommended.
#
# Environment:
#   PYTHON     the Python used to create the environment (default: the first of python3.13, 3.12, 3.11)
#   VENV       environment for the Kai service (default: .venv-kai in the repository); the monitor's
#              is $VENV-monitor
#   PORT       port of the Kai service (default: 8009)
#   KAI_DIR    where the package is kept (default: ~/.cache/agentarmor/kai-35-a3b)
#   HF_HOME    Hugging Face cache for the base model (default: huggingface_hub's own)
#   CONTENT_CUTOFF  deny cutoff for incoming content (default: 0.90, the calibrated value)
set -euo pipefail

KAI_REPO="min-h-wei/Kai-35-A3B"
KAI_REVISION="2b954be06e14b8859ab06c3addd4ee122458ae51"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv-kai}"
PORT="${PORT:-8009}"
KAI_DIR="${KAI_DIR:-$HOME/.cache/agentarmor/kai-35-a3b}"
CONTENT_CUTOFF="${CONTENT_CUTOFF:-0.90}"
MODE="${1:-run}"

say() { printf '==> %s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

case "$MODE" in
  run|--with-monitor|--download-only|--verify-only) ;;
  -h|--help) sed -n '2,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) fail "unknown option $MODE (use --with-monitor, --download-only, --verify-only or no option)" ;;
esac

# 1. The machine.
[ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ] \
  || fail "Kai-35-A3B runs through the MLX backend, which needs Apple silicon (macOS on arm64)"
supported() { "$1" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; }
if [ -z "${PYTHON:-}" ]; then
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null && supported "$candidate"; then PYTHON="$candidate"; break; fi
  done
fi
[ -n "${PYTHON:-}" ] && command -v "$PYTHON" >/dev/null && supported "$PYTHON" \
  || fail "Python 3.11, 3.12 or 3.13 is required (set PYTHON to one)"
mem_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
[ "$mem_gb" -ge 48 ] || fail "this machine has ${mem_gb} GB of memory; the model needs about 25 GB of GPU memory and 48 GB or more in total"
[ "$mem_gb" -ge 64 ] || say "note: ${mem_gb} GB of memory; 64 GB is recommended"

# 2. The environment.
if [ ! -x "$VENV/bin/python" ]; then
  say "creating $VENV"
  "$PYTHON" -m venv "$VENV"
fi
if [ ! -x "$VENV/bin/agentarmor-vllm" ] || ! "$VENV/bin/python" -c 'import mlx_lm, huggingface_hub' 2>/dev/null; then
  say "installing AgentArmor's MLX serving package"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  (cd "$ROOT" && "$VENV/bin/python" -m pip install --quiet './src/vllm[serve-mlx]')
fi

# 3. The downloads. The package goes to real files in KAI_DIR; the base to the Hugging Face cache,
#    where AgentArmor's loader looks for the revision the package names.
say "fetching the Kai-35-A3B package and the base model (the base is about 72 GB on first use)"
KAI_REPO="$KAI_REPO" KAI_REVISION="$KAI_REVISION" KAI_DIR="$KAI_DIR" "$VENV/bin/python" - <<'PY'
import json
import os
import shutil
from pathlib import Path

from huggingface_hub import snapshot_download

repo, revision, target = os.environ["KAI_REPO"], os.environ["KAI_REVISION"], Path(os.environ["KAI_DIR"])
target.mkdir(parents=True, exist_ok=True)
snapshot_download(repo, revision=revision, allow_patterns=["mlx/*"], local_dir=target)
manifest = json.loads((target / "mlx" / "kai.json").read_text())
names = sorted(manifest["base_files"])
need = sum(1 for _ in names)
found = None
try:  # already in the cache? then nothing is downloaded
    found = snapshot_download(manifest["base"], revision=manifest["base_revision"], allow_patterns=names, local_files_only=True)
    if any(not (Path(found) / name).exists() for name in names):
        found = None
except Exception:
    found = None
if found is None:
    free_gb = shutil.disk_usage(Path.home()).free / 1e9
    if free_gb < 80:
        raise SystemExit(f"error: {free_gb:.0f} GB free; the base model needs about 72 GB (80 GB free required)")
    found = snapshot_download(manifest["base"], revision=manifest["base_revision"], allow_patterns=names)
print(f"package: {target / 'mlx'}")
print(f"base:    {found} ({need} files at revision {manifest['base_revision'][:12]})")
PY
[ "$MODE" = "--download-only" ] && { say "downloaded; nothing started"; exit 0; }

# 4. The checks: every package file and every base file against kai.json, the installed MLX
#    versions and sources. No weight is loaded.
say "verifying the package and the base against kai.json (reads about 72 GB; a minute or two)"
"$VENV/bin/agentarmor-vllm" --backend mlx --package "$KAI_DIR/mlx" --verify-only
[ "$MODE" = "--verify-only" ] && { say "verified; nothing started"; exit 0; }

# 5. The Kai service; with --with-monitor, the monitor in front of it.
EXAMPLES="$ROOT/examples/kai-35-a3b"
if [ "$MODE" = "run" ]; then
  say "starting the Kai service on http://127.0.0.1:$PORT (loading takes about half a minute)"
  say "then, in another terminal:"
  printf '      curl --fail http://127.0.0.1:%s/health\n' "$PORT"
  for name in content-attempt content-clean tool-call; do
    printf '      curl --fail http://127.0.0.1:%s/v1/systemone -H "Content-Type: application/json" --data-binary @%s/%s.json\n' "$PORT" "$EXAMPLES" "$name"
  done
  exec "$VENV/bin/agentarmor-vllm" --backend mlx --package "$KAI_DIR/mlx" --port "$PORT"
fi

MONITOR_VENV="${VENV}-monitor"
if [ ! -x "$MONITOR_VENV/bin/agentarmor-monitor" ]; then
  say "installing Summarizer and the monitor in $MONITOR_VENV"
  "$PYTHON" -m venv "$MONITOR_VENV"
  "$MONITOR_VENV/bin/python" -m pip install --quiet --upgrade pip
  (cd "$ROOT" && "$MONITOR_VENV/bin/python" -m pip install --quiet './src/summarizer[runtime]' ./src/monitor)
fi
say "starting the Kai service on http://127.0.0.1:$PORT"
"$VENV/bin/agentarmor-vllm" --backend mlx --package "$KAI_DIR/mlx" --port "$PORT" &
KAI_PID=$!
trap 'kill "$KAI_PID" 2>/dev/null || true' EXIT INT TERM
PACKAGE=""
for _ in $(seq 1 180); do
  kill -0 "$KAI_PID" 2>/dev/null || fail "the Kai service stopped while loading"
  PACKAGE="$(curl --silent --fail "http://127.0.0.1:$PORT/health" 2>/dev/null \
    | "$VENV/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("package_sha256", ""))' 2>/dev/null || true)"
  [ -n "$PACKAGE" ] && break
  sleep 2
done
[ -n "$PACKAGE" ] || fail "the Kai service did not become ready"
say "starting the local monitor (one question; content flagged above $CONTENT_CUTOFF)"
say "in another terminal: $MONITOR_VENV/bin/agentarmor-monitor detections --flagged"
"$MONITOR_VENV/bin/agentarmor-monitor" serve --kai-url "http://127.0.0.1:$PORT" --kai-package-sha256 "$PACKAGE" \
  --kai-contract one-question/1 --kai-content-cutoff "$CONTENT_CUTOFF"
