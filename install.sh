#!/bin/bash
# Download a prebuilt native executable. No compiler, Python, or sudo required.
set -euo pipefail

armor_install() (
  if [[ "$(/usr/bin/uname -s)" != Darwin || "$(/usr/bin/uname -m)" != arm64 ]]; then
    printf 'AgentArmor currently supports native macOS Apple Silicon M4+ only.\n' >&2
    exit 1
  fi
  armor_chip=$(/usr/sbin/sysctl -n machdep.cpu.brand_string)
  if [[ ! "$armor_chip" =~ Apple\ M([0-9]+) ]] || (( BASH_REMATCH[1] < 4 )); then
    printf 'AgentArmor requires Apple Silicon M4 or newer.\n' >&2
    exit 1
  fi
  armor_major=$(/usr/bin/sw_vers -productVersion)
  armor_major=${armor_major%%.*}
  if (( armor_major < 15 )); then
    printf 'AgentArmor requires macOS 15 or newer.\n' >&2
    exit 1
  fi
  armor_version="__AGENTARMOR_VERSION__"
  armor_base='https://github.com/min-mwei/AgentArmor/releases'
  if [[ ! "$armor_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][A-Za-z0-9.-]+)?$ ]]; then
    armor_base="$armor_base/latest/download"
  else
    armor_base="$armor_base/download/v$armor_version"
  fi
  armor_asset=agentarmor-macos-arm64.tar.gz
  armor_temp=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/agentarmor-install.XXXXXX")
  armor_staged=
  trap 'test -z "$armor_staged" || /bin/rm -f "$armor_staged"; /bin/rm -rf "$armor_temp"' EXIT
  armor_curl=(/usr/bin/curl -q -fsSL --proto '=https' --proto-redir '=https'
    --connect-timeout 10 --max-time 120 --max-filesize 134217728)
  "${armor_curl[@]}" "$armor_base/$armor_asset" -o "$armor_temp/$armor_asset"
  "${armor_curl[@]}" "$armor_base/$armor_asset.sha256" -o "$armor_temp/checksum"
  read -r armor_expected armor_filename < "$armor_temp/checksum"
  if [[ ! "$armor_expected" =~ ^[0-9a-f]{64}$ || "$armor_filename" != "$armor_asset" ]]; then
    printf 'Invalid AgentArmor checksum manifest.\n' >&2
    exit 1
  fi
  armor_actual=$(/usr/bin/shasum -a 256 "$armor_temp/$armor_asset")
  armor_actual=${armor_actual%% *}
  if [[ "$armor_actual" != "$armor_expected" ]]; then
    printf 'AgentArmor checksum mismatch; nothing was installed.\n' >&2
    exit 1
  fi
  armor_listing=$(/usr/bin/tar -tzf "$armor_temp/$armor_asset")
  armor_type=$(/usr/bin/tar -tvzf "$armor_temp/$armor_asset")
  if [[ "$armor_listing" != agentarmor || "$armor_type" != -* ]]; then
    printf 'Unexpected AgentArmor archive contents.\n' >&2
    exit 1
  fi
  /usr/bin/tar -xzf "$armor_temp/$armor_asset" -C "$armor_temp"
  if [[ ! -f "$armor_temp/agentarmor" || -L "$armor_temp/agentarmor" ]] ||
     (( $(/usr/bin/stat -f %z "$armor_temp/agentarmor") > 134217728 )); then
    printf 'Invalid AgentArmor executable.\n' >&2
    exit 1
  fi
  /usr/bin/lipo -verify_arch arm64 "$armor_temp/agentarmor"
  armor_destination=${AGENTARMOR_INSTALL_DIR:-"$HOME/.local/bin"}
  /bin/mkdir -p "$armor_destination"
  if [[ -L "$armor_destination/agentarmor" || -d "$armor_destination/agentarmor" ]]; then
    printf 'Refusing to replace a symlink or directory at the install destination.\n' >&2
    exit 1
  fi
  armor_staged=$(/usr/bin/mktemp "$armor_destination/.agentarmor.XXXXXX")
  /usr/bin/install -m 755 "$armor_temp/agentarmor" "$armor_staged"
  /bin/mv -f "$armor_staged" "$armor_destination/agentarmor"
  armor_staged=
  printf 'Installed %s/agentarmor. Add that directory to PATH if needed.\n' "$armor_destination"
  printf 'Initial releases may be unsigned. No quarantine attributes or system security settings were changed.\n'
)

armor_install
