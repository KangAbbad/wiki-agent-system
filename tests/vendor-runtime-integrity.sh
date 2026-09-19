#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
cp -R "$plugin_root" "$test_root/plugin"
printf '\n# tampered fixture\n' >>"$test_root/plugin/vendor/youtube_transcript_api/__init__.py"
mkdir -p "$test_root/data"

set +e
output=$(PLUGIN_ROOT="$test_root/plugin" PLUGIN_DATA="$test_root/data" \
  "$test_root/plugin/hooks/launcher.sh" "$test_root/plugin/hooks/provision.py" 2>&1)
result=$?
set -e

test "$result" -ne 0
printf '%s' "$output" | grep -q 'vendored runtime integrity'
test ! -e "$test_root/data/current"
printf '%s\n' 'vendored runtime tamper regression passed'
