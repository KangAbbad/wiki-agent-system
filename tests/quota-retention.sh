#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
export HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" CODEX_HOME="$test_root/codex"
export MNEMOSYNE_CLI="$test_root/mnemosyne-not-installed" TMPDIR="$test_root/tmp"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$CODEX_HOME" "$TMPDIR"
workspace="$test_root/workspace"
mkdir "$workspace"
printf '%s' "{\"cwd\":\"$workspace\",\"hook_event_name\":\"SessionStart\"}" | "$plugin_root/hooks/launcher.sh" "$plugin_root/hooks/preflight.py" >/dev/null
mkdir -p "$workspace/.wiki/inbox/autosave" "$workspace/.wiki/.sessions/state" "$workspace/.wiki/raw"
printf 'expired autosave\n' >"$workspace/.wiki/inbox/autosave/old.md"
printf 'expired state\n' >"$workspace/.wiki/.sessions/state/old.json"
printf 'canonical evidence\n' >"$workspace/.wiki/raw/keep.md"
touch -t 202001010000 "$workspace/.wiki/inbox/autosave/old.md" "$workspace/.wiki/.sessions/state/old.json" "$workspace/.wiki/raw/keep.md"

receipt_root="$workspace/.wiki/.sessions/wiki-agent-system/youtube-receipts"
caption_path="$workspace/.wiki/inbox/youtube/fixture.vtt"
referenced_receipt_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
unreferenced_receipt_id=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
mkdir -p "$receipt_root" "$(dirname "$caption_path")"
printf 'WEBVTT\n\n00:00.000 --> 00:01.000\nfixture\n' >"$caption_path"
printf '%s\n' "receipt_id: $referenced_receipt_id" >>"$workspace/.wiki/raw/keep.md"
python3 - "$receipt_root" "$caption_path" "$referenced_receipt_id" "$unreferenced_receipt_id" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

receipt_root, caption, referenced, unreferenced = map(Path, sys.argv[1:])
payload = {
    "schema_version": 1,
    "canonical_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "video_id": "dQw4w9WgXcQ",
    "status": "ok",
    "provenance_class": "caption",
    "attempt": 1,
    "attempted_at": "2026-09-16T00:00:00+00:00",
    "files": [{"path": "inbox/youtube/fixture.vtt", "sha256": hashlib.sha256(caption.read_bytes()).hexdigest()}],
}
for receipt_id in (referenced.name, unreferenced.name):
    (receipt_root / f"{receipt_id}.json").write_text(json.dumps(dict(payload, receipt_id=receipt_id)) + "\n")
PY
touch -t 202001010000 "$receipt_root/$referenced_receipt_id.json" "$receipt_root/$unreferenced_receipt_id.json"

"$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/retention.py" "$workspace" --max-bytes 1 >"$test_root/report.json"
grep -q '"level": "block"' "$test_root/report.json"
grep -q 'old.md' "$test_root/report.json"
grep -q '"raw"' "$test_root/report.json"
grep -q "$unreferenced_receipt_id.json" "$test_root/report.json"
! grep -q "$referenced_receipt_id.json" "$test_root/report.json"
test -f "$workspace/.wiki/raw/keep.md"
"$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/retention.py" "$workspace" --apply --max-bytes 1 >"$test_root/apply.json"
test -f "$workspace/.wiki/.trash/autosave/old.md"
test -f "$workspace/.wiki/.trash/state/state/old.json"
test -f "$workspace/.wiki/raw/keep.md"
test -f "$receipt_root/$referenced_receipt_id.json"
test ! -e "$receipt_root/$unreferenced_receipt_id.json"
test -f "$workspace/.wiki/.trash/state/wiki-agent-system/youtube-receipts/$unreferenced_receipt_id.json"

printf '%s\n' "receipt_id: $unreferenced_receipt_id" >>"$workspace/.wiki/raw/keep.md"
touch -t 202001010000 "$workspace/.wiki/.trash/state/wiki-agent-system/youtube-receipts/$unreferenced_receipt_id.json"
"$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/retention.py" "$workspace" --apply --scheduled \
  --autosave-days 1 --queue-days 1 --state-days 1 --trash-days 1 --max-bytes 1 >"$test_root/scheduled.json"
test -f "$workspace/.wiki/.trash/state/wiki-agent-system/youtube-receipts/$unreferenced_receipt_id.json"

printf '%s\n' 'quota retention passed'
