#!/bin/bash
# Re-sign only temporary copies of weight-free tests so leaks can inspect them.
set -euo pipefail
armor_build=${1:?Pass the native build directory}
armor_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
armor_temp=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/agentarmor-leak-check.XXXXXX")
trap '/bin/rm -rf "$armor_temp"' EXIT
for armor_test in armor_model_security server/kaiinfer_server_contract; do
  armor_name=$(/usr/bin/basename "$armor_test")
  /usr/bin/install -m 755 "$armor_build/native/kai/$armor_test" "$armor_temp/$armor_name"
  /usr/bin/codesign --force --sign - --entitlements \
    "$armor_root/native/kai/tests/debug.entitlements.plist" "$armor_temp/$armor_name"
  /usr/bin/leaks -atExit -- "$armor_temp/$armor_name"
done
