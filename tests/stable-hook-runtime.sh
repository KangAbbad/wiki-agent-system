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
sh "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/vendor-runtime-import.sh" "$data_root/current"
fallback_vendor="$test_root/fallback-vendor"
mv "$data_root/current/vendor" "$fallback_vendor"
rm -rf "$runtime_root"
payload=$(printf '{"cwd":"%s","hook_event_name":"SessionStart"}' "$workspace")
printf '%s' "$payload" | PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >"$test_root/output.json"
grep -q 'Workspace knowledge index:' "$test_root/output.json"
resolve=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/scripts/wiki_ambient.py" resolve --cwd "$workspace")
printf '%s' "$resolve" | grep -q '"local_wiki":'
printf '%s' "$resolve" | grep -q '"local_wiki_status": "valid"'

printf '%s\n' 'Stable canonical evidence' >"$test_root/stable-source.md"
stable_result=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" \
  PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" \
  "$data_root/current/hooks/launcher.sh" "$data_root/current/scripts/wiki_ambient.py" canonicalize \
  --cwd "$workspace" --source "$test_root/stable-source.md" \
  --source-url 'https://example.test/stable' --title 'Stable source')
stable_raw=$(printf '%s' "$stable_result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])')
case "$stable_raw" in
  */raw/articles/*) ;;
  *) exit 1 ;;
esac
grep -q '^type: articles$' "$stable_raw"

durable_payload=$(printf '{"cwd":"%s","hook_event_name":"UserPromptSubmit","session_id":"stable-durable","turn_id":"one","prompt":"Research and synthesize the cache-free runtime result"}' "$workspace")
printf '%s' "$durable_payload" | PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >/dev/null
printf '{"cwd":"%s","hook_event_name":"Stop","session_id":"stable-durable","turn_id":"one","last_assistant_message":"Cache-free durable capture"}' "$workspace" | PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >"$test_root/durable-stop.json"
grep -q '"hookEventName": "Stop"' "$test_root/durable-stop.json"
grep -R -q 'Cache-free durable capture' "$workspace/.wiki/inbox/autosave"

capture_output=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" CODEX_SESSION_ID=cache-removed "$data_root/current/hooks/launcher.sh" "$data_root/current/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --scope workspace --outcome 'Cache-removed stable capture' --kind result)
printf '%s' "$capture_output" | grep -q '"status": "pending-curation"'
cache_key=$(python3 -c 'import hashlib; print(hashlib.sha256(b"cache-removed").hexdigest()[:16])')
capture="$workspace/.wiki/inbox/autosave/session-$cache_key.md"
test -f "$capture"

caption_payload=$(printf '{"cwd":"%s","hook_event_name":"UserPromptSubmit","session_id":"stable","turn_id":"caption","prompt":"Riset video https://youtu.be/dQw4w9WgXcQ"}' "$workspace")
printf '%s' "$caption_payload" | PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >"$test_root/caption-context.json"
grep -q 'caption_evidence=verified' "$test_root/caption-context.json"
grep -q 'receipt_id=' "$test_root/caption-context.json"
grep -q 'caption_sha256=' "$test_root/caption-context.json"
grep -q 'transcript=eligible' "$test_root/caption-context.json"
! grep -Fq "$test_root" "$test_root/caption-context.json"

boundary_payload=$(printf '{"cwd":"%s","hook_event_name":"UserPromptSubmit","session_id":"stable-boundary","turn_id":"one","prompt":"Verify production drop database docs https://example.test/spec"}' "$workspace")
printf '%s' "$boundary_payload" | PLUGIN_ROOT="$test_root/missing-plugin" PLUGIN_DATA="$data_root" HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >"$test_root/boundary-context.json"
grep -q 'status=blocked' "$test_root/boundary-context.json"
grep -q 'authority_request=destructive production verification' "$test_root/boundary-context.json"
mv "$fallback_vendor" "$data_root/current/vendor"

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
HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" CODEX_SESSION_ID=rollback "$data_root/current/hooks/launcher.sh" "$data_root/current/scripts/wiki_ambient.py" capture \
  --cwd "$workspace" --outcome 'Rollback-readable capture' --kind result >/dev/null
rollback_key=$(python3 -c 'import hashlib; print(hashlib.sha256(b"rollback").hexdigest()[:16])')
capture="$workspace/.wiki/inbox/autosave/session-$rollback_key.md"
test -f "$capture"

provision "$new_root"
test -f "$data_root/current/hooks/preflight.py"
cmp "$new_root/scripts/evidence_verification.py" "$data_root/current/scripts/evidence_verification.py"
sh "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/vendor-runtime-import.sh" "$data_root/current"
provision "$old_root"
printf '%s' "$old_payload" | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$data_root/current/hooks/launcher.sh" "$data_root/current/hooks/preflight.py" >/dev/null
test -f "$capture"
grep -q 'Rollback-readable capture' "$capture"

printf '%s\n' 'stable hook runtime passed'
