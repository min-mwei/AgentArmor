#!/bin/bash
set -euo pipefail
armor_binary=$1
armor_root=$2
armor_temp=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/agentarmor-release-test.XXXXXX")
trap '/bin/rm -rf "$armor_temp"' EXIT
/bin/bash "$armor_root/scripts/package_macos.sh" --binary "$armor_binary" \
  --version 0.0.0-test --out "$armor_temp/assets"
(
  cd "$armor_temp/assets"
  /usr/bin/shasum -a 256 -c agentarmor-macos-arm64.tar.gz.sha256
)
test "$(/usr/bin/tar -tzf "$armor_temp/assets/agentarmor-macos-arm64.tar.gz")" = agentarmor
/bin/bash -n "$armor_temp/assets/install.sh"
/usr/bin/grep -q 'armor_version="0.0.0-test"' "$armor_temp/assets/install.sh"
if /usr/bin/grep -q '__AGENTARMOR_VERSION__' "$armor_temp/assets/install.sh"; then exit 1; fi
set +e
/bin/bash "$armor_root/scripts/package_macos.sh" --binary "$armor_binary" \
  --version 0.0.0-test --out "$armor_temp/assets" 2>/dev/null
armor_status=$?
set -e
test "$armor_status" -ne 0
