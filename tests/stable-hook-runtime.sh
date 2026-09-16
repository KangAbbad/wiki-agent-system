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
mkdir -p "$test_root/bin" "$test_root/home" "$test_root/config"
cat >"$test_root/bin/yt-dlp" <<'PY'
#!/usr/bin/env python3
from pathlib import Path
import sys

if "--version" in sys.argv:
    print("2026.08.19")
    raise SystemExit(0)

video = "dQw4w9WgXcQ"
template = Path(sys.argv[sys.argv.index("--output") + 1])
target = Path(str(template).replace("%(id)s", video).replace("%(ext)s", "vtt"))
target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
target.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nstable fixture caption\n")
PY
chmod 700 "$test_root/bin/yt-dlp"
export PATH="$test_root/bin:$PATH"

PLUGIN_ROOT="$runtime_root" PLUGIN_DATA="$data_root" "$runtime_root/hooks/launcher.sh" "$runtime_root/hooks/provision.py"
test -f "$data_root/current/hooks/preflight.py"
rm -rf "$runtime_root"
payload=$(printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$workspace")
printf '%s' "$payload" | PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >"$test_root/output.json"
grep -q 'Workspace knowledge index:' "$test_root/output.json"
resolve=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/scripts/wiki_ambient.py" resolve --cwd "$workspace")
printf '%s' "$resolve" | grep -q '"local_wiki":'
printf '%s' "$resolve" | grep -q '"local_wiki_status": "valid"'

capture_output=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --scope workspace --outcome 'Cache-removed stable capture' --kind result)
printf '%s' "$capture_output" | grep -q '"status": "pending-curation"'
capture=$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md')
test -n "$capture"

caption_payload=$(printf '{"cwd":"%s","hook_event_name":"UserPromptSubmit","session_id":"stable","turn_id":"caption","prompt":"Riset video https://youtu.be/dQw4w9WgXcQ"}' "$workspace")
printf '%s' "$caption_payload" | PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >"$test_root/caption-context.json"
grep -q 'caption_evidence=verified' "$test_root/caption-context.json"
grep -q 'receipt_id=' "$test_root/caption-context.json"
grep -q 'caption_sha256=' "$test_root/caption-context.json"
grep -q 'transcript=eligible' "$test_root/caption-context.json"
! grep -Fq "$test_root" "$test_root/caption-context.json"

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
printf '%s' "$old_payload" | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >/dev/null
HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --outcome 'Rollback-readable capture' --kind result >/dev/null
capture=$(find "$workspace/.wiki/inbox/autosave" -type f -name 'session-*.md')
test -n "$capture"

provision "$new_root"
test -f "$data_root/current/hooks/preflight.py"
provision "$old_root"
printf '%s' "$old_payload" | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >/dev/null
test -f "$capture"
grep -q 'Rollback-readable capture' "$capture"

printf '%s\n' 'stable hook runtime passed'
