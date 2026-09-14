#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
runtime_root="$test_root/plugin"
old_root="$test_root/plugin-old"
new_root="$test_root/plugin-new"
data_root="$test_root/data"
workspace="$test_root/workspace"
cp -R "$plugin_root" "$runtime_root"
cp -R "$plugin_root" "$old_root"
cp -R "$plugin_root" "$new_root"
mkdir "$workspace"

PLUGIN_ROOT="$runtime_root" PLUGIN_DATA="$data_root" "$runtime_root/hooks/launcher.sh" "$runtime_root/hooks/provision.py"
test -f "$data_root/current/hooks/preflight.py"
payload=$(printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$workspace")
printf '%s' "$payload" | PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" sh -c '"$PLUGIN_ROOT/hooks/launcher.sh" "$PLUGIN_ROOT/hooks/provision.py" >/dev/null 2>&1 || true; "$PLUGIN_DATA/current/hooks/launcher.sh" "$PLUGIN_DATA/current/hooks/preflight.py"' >"$test_root/output.json"
grep -q 'Workspace knowledge index:' "$test_root/output.json"

set_version() {
  root=$1
  version=$2
  "$root/hooks/launcher.sh" - "$root/.codex-plugin/plugin.json" "$version" <<'PY'
import json
import sys

path, version = sys.argv[1:]
data = json.loads(open(path).read())
data["version"] = version
open(path, "w").write(json.dumps(data))
PY
}

provision() {
  root=$1
  PLUGIN_ROOT="$root" PLUGIN_DATA="$data_root" "$root/hooks/launcher.sh" "$root/hooks/provision.py"
}

set_version "$old_root" 0.3.2
set_version "$new_root" 0.3.3
provision "$old_root"
old_payload=$(printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$workspace")
printf '%s' "$old_payload" | "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >/dev/null
"$data_root/current/hooks/launcher.sh" "$data_root/current/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --outcome 'Rollback-readable capture' --kind result >/dev/null
capture=$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md')
test -n "$capture"

provision "$new_root"
test -f "$data_root/current/hooks/preflight.py"
provision "$old_root"
printf '%s' "$old_payload" | "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >/dev/null
test -f "$capture"
grep -q 'Rollback-readable capture' "$capture"

printf '%s\n' 'stable hook runtime passed'
