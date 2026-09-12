#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
runtime_root="$test_root/plugin"
data_root="$test_root/data"
workspace="$test_root/workspace"
cp -R "$plugin_root" "$runtime_root"
mkdir "$workspace"

PLUGIN_ROOT="$runtime_root" PLUGIN_DATA="$data_root" python3 "$runtime_root/hooks/provision.py"
test -f "$data_root/current/hooks/preflight.py"
payload=$(printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$workspace")
printf '%s' "$payload" | PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" sh -c 'python3 "$PLUGIN_ROOT/hooks/provision.py" >/dev/null 2>&1 || true; python3 "$PLUGIN_DATA/current/hooks/preflight.py"' >"$test_root/output.json"
grep -q 'Workspace knowledge index:' "$test_root/output.json"

printf '%s\n' 'stable hook runtime passed'
