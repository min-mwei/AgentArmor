#!/bin/bash
# Create local release assets; publishing is a separate explicit operation.
set -euo pipefail
armor_binary=
armor_version=
armor_identity=
armor_out=dist
while (( $# )); do
  case "$1" in
    --binary|--version|--identity|--out)
      if (( $# < 2 )); then printf 'Missing argument for %s\n' "$1" >&2; exit 2; fi
      case "$1" in
        --binary) armor_binary=$2 ;;
        --version) armor_version=$2 ;;
        --identity) armor_identity=$2 ;;
        --out) armor_out=$2 ;;
      esac
      shift 2 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
if [[ ! -f "$armor_binary" || ! "$armor_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][A-Za-z0-9.-]+)?$ ]]; then
  printf 'Usage: bash scripts/package_macos.sh --binary PATH --version X.Y.Z [--identity DEVELOPER_ID] [--out DIR]\n' >&2
  exit 2
fi
armor_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
/bin/mkdir -p "$armor_out"
armor_temp=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/agentarmor-package.XXXXXX")
trap '/bin/rm -rf "$armor_temp"' EXIT
/usr/bin/install -m 755 "$armor_binary" "$armor_temp/agentarmor"
/usr/bin/lipo -verify_arch arm64 "$armor_temp/agentarmor"
if /usr/bin/otool -L "$armor_temp/agentarmor" | /usr/bin/grep -Eiq 'libpython|Python.framework'; then
  printf 'Refusing to package a Python-dependent executable.\n' >&2
  exit 1
fi
if [[ -n "$armor_identity" ]]; then
  /usr/bin/codesign --force --options runtime --timestamp --identifier com.agentarmor.cli \
    --sign "$armor_identity" "$armor_temp/agentarmor"
  /usr/bin/codesign --verify --strict --verbose=2 "$armor_temp/agentarmor"
fi
# Deliberately no notarization or stapling step.
armor_asset=agentarmor-macos-arm64.tar.gz
if [[ -e "$armor_out/$armor_asset" || -e "$armor_out/install.sh" ]]; then
  printf 'Refusing to overwrite existing release assets; use a new output directory.\n' >&2
  exit 1
fi
/usr/bin/tar -czf "$armor_out/$armor_asset" -C "$armor_temp" agentarmor
(cd "$armor_out" && /usr/bin/shasum -a 256 "$armor_asset" > "$armor_asset.sha256")
/usr/bin/sed "s/__AGENTARMOR_VERSION__/$armor_version/g" "$armor_root/install.sh" > "$armor_out/install.sh"
/bin/chmod 755 "$armor_out/install.sh"
printf 'Prepared local %s assets in %s (signing %s). Nothing was uploaded.\n' \
  "$armor_version" "$armor_out" "${armor_identity:-not requested}"
