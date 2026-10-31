#!/bin/bash
# Exercise the real installer with a local release mirror; never use the network.
set -euo pipefail
armor_binary=$1
armor_root=$2
armor_temp=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/agentarmor-installer-test.XXXXXX")
trap '/bin/rm -rf "$armor_temp"' EXIT
/bin/bash "$armor_root/scripts/package_macos.sh" --binary "$armor_binary" \
  --version 0.0.0-test --out "$armor_temp/assets"
/usr/bin/sed 's|/usr/bin/curl|"$ARMOR_INSTALL_TEST_CURL"|g' \
  "$armor_temp/assets/install.sh" > "$armor_temp/install.sh"
# These mock build artifacts are generated only by the test.
/usr/bin/printf '%s\n' '#!/bin/bash' 'set -eu' \
  'while (( $# )); do case "$1" in https:*) url=$1; shift ;; -o) dest=$2; shift 2 ;; *) shift ;; esac; done' \
  'asset=$(/usr/bin/basename "$url"); /bin/cp "$ARMOR_INSTALL_TEST_ASSETS/$asset" "$dest"' > "$armor_temp/curl"
/bin/chmod 700 "$armor_temp/curl"
export ARMOR_INSTALL_TEST_CURL="$armor_temp/curl"
export ARMOR_INSTALL_TEST_ASSETS="$armor_temp/assets"
export AGENTARMOR_INSTALL_DIR="$armor_temp/bin"
/bin/bash "$armor_temp/install.sh"
test -x "$armor_temp/bin/agentarmor"
armor_before=$(/usr/bin/shasum -a 256 "$armor_temp/bin/agentarmor")
/usr/bin/printf '%064d  agentarmor-macos-arm64.tar.gz\n' 0 > "$armor_temp/assets/agentarmor-macos-arm64.tar.gz.sha256"
set +e
/bin/bash "$armor_temp/install.sh" > "$armor_temp/output" 2>&1
armor_status=$?
set -e
test "$armor_status" -ne 0
test "$(/usr/bin/shasum -a 256 "$armor_temp/bin/agentarmor")" = "$armor_before"
/usr/bin/grep -q 'checksum mismatch' "$armor_temp/output"

# A valid checksum must not make an archive with extra paths acceptable.
/usr/bin/touch "$armor_temp/unexpected"
/usr/bin/tar -czf "$armor_temp/assets/agentarmor-macos-arm64.tar.gz" -C "$armor_temp" unexpected
(cd "$armor_temp/assets" && /usr/bin/shasum -a 256 agentarmor-macos-arm64.tar.gz > agentarmor-macos-arm64.tar.gz.sha256)
set +e
/bin/bash "$armor_temp/install.sh" > "$armor_temp/output" 2>&1
armor_status=$?
set -e
test "$armor_status" -ne 0
test "$(/usr/bin/shasum -a 256 "$armor_temp/bin/agentarmor")" = "$armor_before"
/usr/bin/grep -q 'Unexpected AgentArmor archive contents' "$armor_temp/output"
