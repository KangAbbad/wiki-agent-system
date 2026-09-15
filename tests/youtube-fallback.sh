#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
launcher="$root/plugins/wiki-preflight/hooks/launcher.sh"
script="$root/plugins/wiki-preflight/scripts/youtube_fallback.py"
hook="$root/plugins/wiki-preflight/hooks/preflight.py"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

"$launcher" "$script" self-test
help=$({ "$launcher" "$script" --help; } 2>&1)
printf '%s' "$help" | grep -q 'Fetch YouTube captions through yt-dlp'
captions_help=$({ "$launcher" "$script" captions --help; } 2>&1)
printf '%s' "$captions_help" | grep -q -- '--approve-install'
! printf '%s' "$captions_help" | grep -q -- '--auto-install'

set +e
invalid=$({ "$launcher" "$script" captions 'https://example.com/watch?v=dQw4w9WgXcQ'; } 2>&1)
status=$?
set -e
test "$status" -eq 2
printf '%s' "$invalid" | grep -q 'only YouTube video URLs'

mkdir -p "$test_root/bin" "$test_root/home" "$test_root/config"
cat >"$test_root/bin/yt-dlp" <<'PY'
#!/usr/bin/env python3
import json
import os
import time
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

def video_id(url):
    parsed = urlparse(url)
    if parsed.hostname in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/", 1)[0]
    return parse_qs(parsed.query).get("v", [""])[0]

if "--version" in sys.argv:
    print("2026.08.19")
    raise SystemExit(0)
if "--approve-install" in sys.argv:
    print("automatic installation approval is forbidden", file=sys.stderr)
    raise SystemExit(1)
if os.environ.get("FAKE_YTDLP_LOG"):
    with open(os.environ["FAKE_YTDLP_LOG"], "a") as log:
        log.write(video_id(sys.argv[-1]) + "\n")
delay_name = "FAKE_YTDLP_METADATA_DELAY" if "--dump-single-json" in sys.argv else "FAKE_YTDLP_CAPTION_DELAY"
delay = os.environ.get(delay_name) or os.environ.get("FAKE_YTDLP_DELAY")
if delay:
    time.sleep(float(delay))
if os.environ.get("FAKE_YTDLP_FAIL") == "1":
    if "--dump-single-json" in sys.argv:
        print("simulated metadata failure", file=sys.stderr)
        raise SystemExit(1)
    video = video_id(sys.argv[-1])
    template = Path(sys.argv[sys.argv.index("--output") + 1])
    target = Path(str(template).replace("%(id)s", video).replace("%(ext)s", "vtt"))
    if target.exists():
        target.write_text(target.read_text())
    residue = target.with_name(f"{video}.failed.vtt")
    residue.write_text("WEBVTT\n\nfailed attempt\n")
    if os.environ.get("FAKE_YTDLP_SYMLINK") == "1":
        residue.with_name("dQw4w9WgXcQ.link.vtt").symlink_to(os.environ["FAKE_SENTINEL"])
    print("simulated yt-dlp failure", file=sys.stderr)
    raise SystemExit(1)

if "--dump-single-json" in sys.argv:
    if os.environ.get("FAKE_YTDLP_METADATA") != "1":
        print("simulated metadata unavailable", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({
        "title": "Fixture metadata",
        "uploader": "Fixture channel",
        "channel": "Fixture channel",
        "upload_date": "20260915",
        "duration": 42,
        "description": "Metadata description https://example.test/page?tracking=ignored",
    }))
    raise SystemExit(0)

if os.environ.get("FAKE_YTDLP_NO_CAPTIONS") == "1":
    raise SystemExit(0)

video = video_id(sys.argv[-1])
template = Path(sys.argv[sys.argv.index("--output") + 1])
target = Path(str(template).replace("%(id)s", video).replace("%(ext)s", "vtt"))
if not target.exists():
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nfixture caption\n")
PY
chmod 700 "$test_root/bin/yt-dlp"
export PATH="$test_root/bin:$PATH"

workspace="$test_root/workspace"
mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"
output_dir="$workspace/.wiki/inbox/youtube"
url='https://youtu.be/dQw4w9WgXcQ?si=ignored'

first=$(cd "$workspace" && "$launcher" "$script" captions "$url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$output_dir")
printf '%s' "$first" | grep -q '"status": "ok"'
test -f "$output_dir/dQw4w9WgXcQ.vtt"

second=$(cd "$workspace" && "$launcher" "$script" captions "$url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$output_dir")
printf '%s' "$second" | grep -q '"status": "stale-captions-ignored"'
printf '%s' "$second" | grep -q '"files": \[\]'
printf '%s' "$second" | grep -q 'existing_files_ignored'
printf '%s' "$second" | grep -q '"provenance_class": "none"'
printf '%s' "$second" | grep -q '"evidence_eligible": false'
printf '%s' "$second" | grep -q '"transcript_eligible": false'

arbitrary="$test_root/arbitrary-output"
set +e
arbitrary_result=$(cd "$workspace" && "$launcher" "$script" captions "$url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$arbitrary" 2>&1)
status=$?
set -e
test "$status" -eq 2
printf '%s' "$arbitrary_result" | grep -q 'active .wiki/inbox/youtube directory'
test ! -e "$arbitrary"

set +e
failed_result=$(cd "$workspace" && FAKE_YTDLP_FAIL=1 "$launcher" "$script" captions "$url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$output_dir" 2>&1)
status=$?
set -e
test "$status" -eq 1
printf '%s' "$failed_result" | grep -q '"status": "error"'
printf '%s' "$failed_result" | grep -q '"files": \[\]'
! printf '%s' "$failed_result" | grep -q '"status": "partial"'
test ! -e "$output_dir/dQw4w9WgXcQ.vtt"
test ! -e "$output_dir/dQw4w9WgXcQ.failed.vtt"

sentinel="$test_root/sentinel.txt"
printf '%s\n' sentinel >"$sentinel"
set +e
symlink_result=$(cd "$workspace" && FAKE_YTDLP_FAIL=1 FAKE_YTDLP_SYMLINK=1 FAKE_SENTINEL="$sentinel" "$launcher" "$script" captions "$url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$output_dir" 2>&1)
status=$?
set -e
test "$status" -eq 1
printf '%s' "$symlink_result" | grep -q '"status": "error"'
test ! -L "$output_dir/dQw4w9WgXcQ.link.vtt"
test -f "$sentinel"
grep -q '^sentinel$' "$sentinel"

foreign="$test_root/foreign"
mkdir -p "$foreign/.wiki/raw" "$foreign/.wiki/wiki" "$foreign/.wiki/inbox/youtube"
printf '%s\n' '# Foreign Wiki' >"$foreign/.wiki/config.md"
printf '%s\n' '# Foreign Wiki' >"$foreign/.wiki/_index.md"
set +e
foreign_result=$(cd "$workspace" && "$launcher" "$script" captions "$url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$foreign/.wiki/inbox/youtube" 2>&1)
status=$?
set -e
test "$status" -eq 2
printf '%s' "$foreign_result" | grep -q 'foreign or incomplete .wiki'
test ! -e "$foreign/.wiki/inbox/youtube/dQw4w9WgXcQ.vtt"

set +e
invalid_timeout=$(cd "$workspace" && "$launcher" "$script" captions "$url" --timeout 0 2>&1)
status=$?
set -e
test "$status" -eq 2
printf '%s' "$invalid_timeout" | grep -q -- '--timeout must be between'

"$launcher" - "$script" <<'PY'
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("youtube_fallback", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.locate_ytdlp = lambda: None
_, error = module.ensure_ytdlp(False)
assert "explicit approval" in error
name, command, _ = module.installer()
assert name == "pip"
assert command[-1] == "yt-dlp==2026.08.19"
assert "brew" not in " ".join(command)
PY

metadata_workspace="$test_root/metadata-workspace"
metadata_log="$test_root/metadata-fetches.log"
mkdir -p "$metadata_workspace/.wiki/raw" "$metadata_workspace/.wiki/wiki" "$metadata_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$metadata_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$metadata_workspace/.wiki/_index.md"
metadata_output_dir="$metadata_workspace/.wiki/inbox/youtube"
metadata_url='https://youtu.be/tGJTzahuapo?si=tracking-query&feature=share'
metadata_result=$(cd "$metadata_workspace" && FAKE_YTDLP_METADATA=1 FAKE_YTDLP_NO_CAPTIONS=1 FAKE_YTDLP_LOG="$metadata_log" "$launcher" "$script" captions "$metadata_url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$metadata_output_dir")
printf '%s' "$metadata_result" | grep -q '"status": "metadata-only"'
printf '%s' "$metadata_result" | grep -q '"provenance_class": "metadata"'
printf '%s' "$metadata_result" | grep -q '"evidence_eligible": true'
printf '%s' "$metadata_result" | grep -q '"transcript_eligible": false'
printf '%s' "$metadata_result" | grep -q '"title": "Fixture metadata"'
printf '%s' "$metadata_result" | grep -q '"description": "Metadata description https://example.test/page"'
! printf '%s' "$metadata_result" | grep -Fq 'tracking-query'
test ! -e "$metadata_output_dir/tGJTzahuapo.vtt"
test "$(wc -l <"$metadata_log" | tr -d ' ')" -eq 2

metadata_queue_workspace="$test_root/metadata-queue-workspace"
mkdir -p "$metadata_queue_workspace/.wiki/raw" "$metadata_queue_workspace/.wiki/wiki" "$metadata_queue_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$metadata_queue_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$metadata_queue_workspace/.wiki/_index.md"
metadata_queue_prompt='Riset video https://www.youtube.com/watch?v=tGJTzahuapo&si=tracking-query'
metadata_queue_output=$(printf '%s' "{\"cwd\":\"$metadata_queue_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"metadata\",\"turn_id\":\"one\",\"prompt\":\"$metadata_queue_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_METADATA=1 FAKE_YTDLP_NO_CAPTIONS=1 "$launcher" "$hook")
printf '%s' "$metadata_queue_output" | grep -q 'status=metadata-only'
printf '%s' "$metadata_queue_output" | grep -q 'provenance=metadata'
printf '%s' "$metadata_queue_output" | grep -q 'transcript=not-available'
metadata_queue_record=$(find "$metadata_queue_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$metadata_queue_record" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert len(data["items"]) == 1
item = data["items"][0]
assert item["status"] == "metadata-only"
assert item["url"] == "https://www.youtube.com/watch?v=tGJTzahuapo"
assert item["provenance_class"] == "metadata"
assert item["evidence_eligible"] is True
assert item["transcript_eligible"] is False
assert item["files"] == []
assert item["metadata"]["title"] == "Fixture metadata"
assert "tracking-query" not in json.dumps(item)
assert all(attempt["route"] in {"caption", "metadata"} for attempt in item["attempts"])
PY

hook_workspace="$test_root/hook-workspace"
hook_log="$test_root/hook-fetches.log"
mkdir -p "$hook_workspace/.wiki/raw" "$hook_workspace/.wiki/wiki" "$hook_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$hook_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$hook_workspace/.wiki/_index.md"
historical_prompt='Tolong riset dan buat knowledge base berbahasa Indonesia dari empat video YouTube berikut: https://www.youtube.com/watch?v=S78sl3d8D1I https://www.youtube.com/watch?v=DEG-k0r9C2E https://www.youtube.com/watch?v=tGJTzahuapo https://www.youtube.com/watch?v=62qCljKilH8'
hook_output=$(printf '%s' "{\"cwd\":\"$hook_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"youtube-hook\",\"turn_id\":\"one\",\"prompt\":\"$historical_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_LOG="$hook_log" "$launcher" "$hook")
printf '%s' "$hook_output" | grep -q 'YouTube automatic fallback preflight'
printf '%s' "$hook_output" | grep -q 'status=ok'
printf '%s' "$hook_output" | grep -q 'queued=2 additional YouTube URL(s)'
! printf '%s' "$hook_output" | grep -Fq 'skipped='
! printf '%s' "$hook_output" | grep -Fq 'process on demand'
! printf '%s' "$hook_output" | grep -Fq '$PLUGIN_ROOT'
! printf '%s' "$hook_output" | grep -Fq '$PLUGIN_DATA'
test -f "$hook_workspace/.wiki/inbox/youtube/S78sl3d8D1I.vtt"
test -f "$hook_workspace/.wiki/inbox/youtube/DEG-k0r9C2E.vtt"
queue_record=$(find "$hook_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
test -n "$queue_record"
python3 - "$queue_record" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
assert data["schema_version"] == 2
assert len(data["items"]) == 4
assert all(item["status"] in {"ok", "no-captions", "metadata-only", "error", "blocked-install"} for item in data["items"])
assert all(item["attempt"] == 1 for item in data["items"])
assert all(item["provenance_class"] == "caption" for item in data["items"])
assert all(item["evidence_eligible"] is True for item in data["items"])
assert all(item["transcript_eligible"] is True for item in data["items"])
assert all(item["evidence_reason"] == "fresh-regular-vtt" for item in data["items"])
assert all(item["attempts"] and item["attempts"][0]["route"] == "caption" for item in data["items"])
assert all("session" not in json.dumps(item).lower() for item in data["items"])
assert not any(key in json.dumps(data) for key in ("PLUGIN_ROOT", "PLUGIN_DATA"))
PY
test "$(wc -l <"$hook_log" | tr -d ' ')" -eq 4

race_url_one='https://www.youtube.com/watch?v=S78sl3d8D1I'
race_url_two='https://www.youtube.com/watch?v=DEG-k0r9C2E'
slow_workspace="$test_root/slow-hook-workspace"
mkdir -p "$slow_workspace/.wiki/raw" "$slow_workspace/.wiki/wiki" "$slow_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$slow_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$slow_workspace/.wiki/_index.md"
slow_started=$(date +%s)
slow_output=$(printf '%s' "{\"cwd\":\"$slow_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"slow-hook\",\"turn_id\":\"one\",\"prompt\":\"$historical_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_CAPTION_DELAY=11 FAKE_YTDLP_METADATA_DELAY=15 FAKE_YTDLP_NO_CAPTIONS=1 "$launcher" "$hook")
slow_elapsed=$(( $(date +%s) - slow_started ))
test "$slow_elapsed" -lt 45
printf '%s' "$slow_output" | grep -q 'status=error'
printf '%s' "$slow_output" | grep -q 'terminal=4; pending=0'
slow_record=$(find "$slow_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$slow_record" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert data["schema_version"] == 2
assert len(data["items"]) == 4
assert all(item["status"] == "error" for item in data["items"])
assert all(item["transcript_eligible"] is False for item in data["items"])
PY

legacy_workspace="$test_root/legacy-workspace"
mkdir -p "$legacy_workspace/.wiki/raw" "$legacy_workspace/.wiki/wiki" "$legacy_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$legacy_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$legacy_workspace/.wiki/_index.md"
legacy_queue=$("$launcher" "$script" queue --workspace "$legacy_workspace" --session-id legacy --turn-id one "$race_url_one")
legacy_queue_id=$(printf '%s' "$legacy_queue" | python3 -c 'import json,sys; print(json.load(sys.stdin)["queue_id"])')
legacy_record=$(find "$legacy_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$legacy_record" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
data["schema_version"] = 1
for item in data["items"]:
    for field in ("provenance_class", "evidence_eligible", "transcript_eligible", "evidence_reason", "attempts", "metadata"):
        item.pop(field, None)
open(path, "w").write(json.dumps(data, sort_keys=True) + "\n")
PY
legacy_before=$(shasum -a 256 "$legacy_record" | awk '{print $1}')
legacy_queue_after=$("$launcher" "$script" queue --workspace "$legacy_workspace" --session-id legacy --turn-id one "$race_url_one")
printf '%s' "$legacy_queue_after" | grep -q '"status": "ok"'
python3 - "$legacy_record" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert data["schema_version"] == 2
assert len(data["items"]) == 1
item = data["items"][0]
assert {"provenance_class", "evidence_eligible", "transcript_eligible", "evidence_reason", "attempts", "metadata"} <= set(item)
PY
legacy_after=$(shasum -a 256 "$legacy_record" | awk '{print $1}')
test "$legacy_before" != "$legacy_after"
test -z "$(find "$(dirname "$legacy_record")" -maxdepth 1 -name '.queue-*' -print -quit)"
legacy_future_before=$(shasum -a 256 "$legacy_record" | awk '{print $1}')
"$launcher" - "$script" "$legacy_record" "$legacy_queue_id" "$legacy_workspace" <<'PY'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("youtube_fallback_old", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.QUEUE_SCHEMA_VERSION = 1
try:
    module.ensure_queue(Path(sys.argv[4]) / ".wiki", ["https://www.youtube.com/watch?v=S78sl3d8D1I"], sys.argv[3])
except module.QueueError as error:
    assert "newer than this runtime" in str(error)
else:
    raise AssertionError("old runtime accepted schema 2")
PY
legacy_future_after=$(shasum -a 256 "$legacy_record" | awk '{print $1}')
test "$legacy_future_before" = "$legacy_future_after"

no_identity_workspace="$test_root/no-identity-workspace"
no_identity_log="$test_root/no-identity-fetches.log"
mkdir -p "$no_identity_workspace/.wiki/raw" "$no_identity_workspace/.wiki/wiki" "$no_identity_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$no_identity_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$no_identity_workspace/.wiki/_index.md"
no_identity_prompt='Riset video https://www.youtube.com/watch?v=S78sl3d8D1I'
no_identity_first=$(printf '%s' "{\"cwd\":\"$no_identity_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"$no_identity_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_LOG="$no_identity_log" "$launcher" "$hook")
no_identity_second=$(printf '%s' "{\"cwd\":\"$no_identity_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"\",\"turn_id\":\"\",\"prompt\":\"$no_identity_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_LOG="$no_identity_log" "$launcher" "$hook")
printf '%s' "$no_identity_first" | grep -q 'queue: status=unavailable; reason=queue requires complete session and turn ids'
printf '%s' "$no_identity_second" | grep -q 'queue: status=unavailable; reason=queue requires complete session and turn ids'
test ! -e "$no_identity_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues"
test ! -e "$no_identity_workspace/.wiki/inbox/youtube/S78sl3d8D1I.vtt"
test ! -e "$no_identity_log"

repeat_output=$(printf '%s' "{\"cwd\":\"$hook_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"youtube-hook\",\"turn_id\":\"one\",\"prompt\":\"$historical_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_LOG="$hook_log" "$launcher" "$hook")
printf '%s' "$repeat_output" | grep -q 'terminal=4; pending=0'
test "$(wc -l <"$hook_log" | tr -d ' ')" -eq 4

queue_id=$(python3 - "$queue_record" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1]))["queue_id"])
PY
)
retry_url='https://www.youtube.com/watch?v=S78sl3d8D1I'
"$launcher" "$script" retry --workspace "$hook_workspace" --queue-id "$queue_id" "$retry_url" >/dev/null
FAKE_YTDLP_LOG="$hook_log" "$launcher" "$script" drain --workspace "$hook_workspace" --queue-id "$queue_id" --deadline 10 --limit 1 >/dev/null
test "$(wc -l <"$hook_log" | tr -d ' ')" -eq 6
python3 - "$queue_record" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
items = {item["video_id"]: item for item in data["items"]}
assert items["S78sl3d8D1I"]["status"] == "no-captions"
assert items["S78sl3d8D1I"]["attempt"] == 2
assert items["S78sl3d8D1I"]["retry_count"] == 1
assert all(item["attempt"] == 1 for key, item in items.items() if key != "S78sl3d8D1I")
PY

race_workspace="$test_root/race-workspace"
mkdir -p "$race_workspace/.wiki/raw" "$race_workspace/.wiki/wiki" "$race_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$race_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$race_workspace/.wiki/_index.md"
race_queue=$("$launcher" "$script" queue --workspace "$race_workspace" --session-id race --turn-id one "$race_url_one" "$race_url_two")
race_queue_id=$(printf '%s' "$race_queue" | python3 -c 'import json,sys; print(json.load(sys.stdin)["queue_id"])')
race_log="$test_root/race-fetches.log"
FAKE_YTDLP_LOG="$race_log" FAKE_YTDLP_DELAY=0.3 "$launcher" "$script" drain --workspace "$race_workspace" --queue-id "$race_queue_id" --deadline 10 --limit 2 >/dev/null &
first_drain=$!
FAKE_YTDLP_LOG="$race_log" FAKE_YTDLP_DELAY=0.3 "$launcher" "$script" drain --workspace "$race_workspace" --queue-id "$race_queue_id" --deadline 10 --limit 2 >/dev/null &
second_drain=$!
wait "$first_drain"
wait "$second_drain"
test "$(wc -l <"$race_log" | tr -d ' ')" -eq 2

future_workspace="$test_root/future-workspace"
mkdir -p "$future_workspace/.wiki/raw" "$future_workspace/.wiki/wiki" "$future_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$future_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$future_workspace/.wiki/_index.md"
future_queue=$("$launcher" "$script" queue --workspace "$future_workspace" --session-id future --turn-id one "$race_url_one")
future_queue_id=$(printf '%s' "$future_queue" | python3 -c 'import json,sys; print(json.load(sys.stdin)["queue_id"])')
future_record=$(find "$future_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$future_record" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
data["schema_version"] = 99
open(path, "w").write(json.dumps(data, sort_keys=True) + "\n")
PY
future_lock=$(dirname "$future_record")/$(basename "$future_record" .json).lock
rm "$future_lock"
future_before=$(shasum -a 256 "$future_record" | awk '{print $1}')
set +e
future_result=$("$launcher" "$script" drain --workspace "$future_workspace" --queue-id "$future_queue_id" --deadline 10 --limit 1 2>&1)
future_status=$?
set -e
test "$future_status" -eq 2
printf '%s' "$future_result" | grep -q 'newer than this runtime'
future_after=$(shasum -a 256 "$future_record" | awk '{print $1}')
test "$future_before" = "$future_after"
test ! -e "$future_lock"

pending_workspace="$test_root/pending-workspace"
mkdir -p "$pending_workspace/.wiki/raw" "$pending_workspace/.wiki/wiki" "$pending_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$pending_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$pending_workspace/.wiki/_index.md"
pending_prompt='Riset lima video: https://www.youtube.com/watch?v=S78sl3d8D1I https://www.youtube.com/watch?v=DEG-k0r9C2E https://www.youtube.com/watch?v=tGJTzahuapo https://www.youtube.com/watch?v=62qCljKilH8 https://www.youtube.com/watch?v=dQw4w9WgXcQ'
pending_output=$(printf '%s' "{\"cwd\":\"$pending_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"pending\",\"turn_id\":\"one\",\"prompt\":\"$pending_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_LOG="$test_root/pending-fetches.log" "$launcher" "$hook")
printf '%s' "$pending_output" | grep -q 'terminal=4; pending=1'
pending_record=$(find "$pending_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$pending_record" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert len(data["items"]) == 5
assert sum(item["status"] == "pending" for item in data["items"]) == 1
PY

retention_workspace="$test_root/retention-workspace"
mkdir -p "$retention_workspace/.wiki/raw" "$retention_workspace/.wiki/wiki" "$retention_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$retention_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$retention_workspace/.wiki/_index.md"
retention_queue=$("$launcher" "$script" queue --workspace "$retention_workspace" --session-id retention --turn-id one "$race_url_one")
retention_queue_id=$(printf '%s' "$retention_queue" | python3 -c 'import json,sys; print(json.load(sys.stdin)["queue_id"])')
retention_record=$(find "$retention_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
touch -t 202001010000 "$retention_record"
XDG_CONFIG_HOME="$test_root/config" "$launcher" "$root/plugins/wiki-preflight/scripts/retention.py" "$retention_workspace" \
  --apply --queue-days 1 --state-days 30 >/dev/null
test ! -e "$retention_record"
test -f "$retention_workspace/.wiki/.trash/state/wiki-agent-system/youtube-queues/$retention_queue_id.json"

test ! -e "$hook_workspace/.gitignore"

policy="$root/plugins/wiki-preflight/defaults/policy.md"
ambient="$root/plugins/wiki-preflight/skills/wiki-ambient/SKILL.md"
workspace_skill="$root/plugins/wiki-preflight/skills/wiki-workspace/SKILL.md"
grep -Fq '`UserPromptSubmit` owns ordinary YouTube ingestion' "$policy"
grep -Fq 'automatic queue drain' "$policy"
for document in "$policy" "$ambient" "$workspace_skill"; do
  ! grep -Fq '$PLUGIN_ROOT' "$document"
  ! grep -Fq '$PLUGIN_DATA' "$document"
done

grep -q '"timeout": 45' "$root/plugins/wiki-preflight/hooks/hooks.json"

printf '%s\n' 'youtube fallback test passed'
