#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
export HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" CODEX_HOME="$test_root/codex"
export MNEMOSYNE_CLI="$test_root/mnemosyne-not-installed" TMPDIR="$test_root/tmp"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$TMPDIR"
test_plugin="$test_root/plugin"
cp -R "$root/plugins/wiki-preflight" "$test_plugin"
rm -r "$test_plugin/vendor"
launcher="$test_plugin/hooks/launcher.sh"
script="$test_plugin/scripts/youtube_fallback.py"
hook="$test_plugin/hooks/preflight.py"

context_only() {
  python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])'
}

"$launcher" "$script" self-test
help=$({ "$launcher" "$script" --help; } 2>&1)
printf '%s' "$help" | grep -q 'Fetch YouTube captions and explicitly agent-owned local transcriptions'
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
fail_once_file = os.environ.get("FAKE_YTDLP_FAIL_ONCE_FILE")
fail_once = bool(fail_once_file) and "--dump-single-json" not in sys.argv and not Path(fail_once_file).exists()
if fail_once:
    Path(fail_once_file).write_text("failed once\n")
if os.environ.get("FAKE_YTDLP_FAIL") == "1" or fail_once or (
    os.environ.get("FAKE_YTDLP_FAIL_CAPTION") == "1" and "--dump-single-json" not in sys.argv
):
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

if "--format" in sys.argv:
    template = Path(sys.argv[sys.argv.index("--output") + 1])
    target = Path(str(template).replace("%(ext)s", "webm"))
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text("fixture audio")
    raise SystemExit(0)

if os.environ.get("FAKE_YTDLP_NO_CAPTIONS") == "1":
    raise SystemExit(0)

video = video_id(sys.argv[-1])
template = Path(sys.argv[sys.argv.index("--output") + 1])
target = Path(str(template).replace("%(id)s", video).replace("%(ext)s", "vtt"))
if os.environ.get("FAKE_YTDLP_SUCCESS_SYMLINK") == "1":
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.symlink_to(os.environ["FAKE_SENTINEL"])
    raise SystemExit(0)
if not target.exists():
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nfixture caption\n")
PY
chmod 700 "$test_root/bin/yt-dlp"
cat >"$test_root/bin/ffmpeg" <<'PY'
#!/usr/bin/env python3
from pathlib import Path
import sys
if __import__("os").environ.get("FAKE_FFMPEG_FAIL") == "1":
    raise SystemExit(1)
Path(sys.argv[-1]).write_bytes(b"RIFF fixture wav")
PY
chmod 700 "$test_root/bin/ffmpeg"
cat >"$test_root/bin/whisper-cli" <<'PY'
#!/usr/bin/env python3
from pathlib import Path
import os, sys
target = Path(sys.argv[sys.argv.index("-of") + 1] + ".txt")
if os.environ.get("FAKE_WHISPER_FAIL") == "1":
    target.write_text("incomplete")
    raise SystemExit(1)
target.write_text("transkripsi mesin fixture\n")
PY
chmod 700 "$test_root/bin/whisper-cli"
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
first_receipt=$(find "$workspace/.wiki/.sessions/wiki-agent-system/youtube-receipts" -type f -name '*.json' -print)
test -n "$first_receipt"
first_receipt_id=$(printf '%s' "$first" | python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt"]["receipt_id"])')
python3 - "$first_receipt" "$output_dir/dQw4w9WgXcQ.vtt" "$workspace" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

receipt_path, caption_path, workspace = map(Path, sys.argv[1:])
data = json.loads(receipt_path.read_text())
assert set(data) == {
    "schema_version", "receipt_id", "canonical_url", "video_id", "status",
    "provenance_class", "attempt", "attempted_at", "files",
}
assert data["schema_version"] == 1
assert data["status"] == "ok"
assert data["provenance_class"] == "caption"
assert data["canonical_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
assert data["files"] == [{
    "path": "inbox/youtube/dQw4w9WgXcQ.vtt",
    "sha256": hashlib.sha256(caption_path.read_bytes()).hexdigest(),
}]
assert str(workspace) not in json.dumps(data)
PY
! printf '%s' "$first" | grep -Fq "$workspace"
printf '%s' "$first" | grep -q '"sha256":'

ambient_script="$root/plugins/wiki-preflight/scripts/wiki_ambient.py"
receipt_backup="$test_root/first-receipt.json"
cp "$first_receipt" "$receipt_backup"
canonical_result=$(cd "$workspace" && HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$ambient_script" canonicalize \
  --cwd "$workspace" --source "$output_dir/dQw4w9WgXcQ.vtt" --source-url "$url" \
  --title 'Caption fixture' --receipt "$first_receipt_id")
printf '%s' "$canonical_result" | grep -q 'canonical-evidence'
printf '%s' "$canonical_result" | grep -q "\"receipt_id\": \"$first_receipt_id\""
caption_raw=$(find "$workspace/.wiki/raw" -type f -name 'caption-fixture-*.md')
case "$caption_raw" in
  */raw/articles/*) ;;
  *) exit 1 ;;
esac
grep -q '^type: articles$' "$caption_raw"
grep -q "^receipt_id: $first_receipt_id$" "$caption_raw"
grep -q '^provenance_class: caption$' "$caption_raw"
caption_digest=$(shasum -a 256 "$output_dir/dQw4w9WgXcQ.vtt" | awk '{print $1}')
caption_prefix=$(printf '%s' "$caption_digest" | cut -c1-12)
unbound_raw="$workspace/.wiki/raw/unbound-existing-$caption_prefix.md"
printf '%s\n' '---' 'type: raw-source' "content_sha256: $caption_digest" '---' >"$unbound_raw"
set +e
unbound_result=$(cd "$workspace" && HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$ambient_script" canonicalize \
  --cwd "$workspace" --source "$output_dir/dQw4w9WgXcQ.vtt" --source-url "$url" \
  --title 'Unbound existing' --receipt "$first_receipt_id" 2>&1)
unbound_status=$?
set -e
test "$unbound_status" -ne 0
printf '%s' "$unbound_result" | grep -q 'not receipt-bound'

set +e
missing_receipt_result=$(cd "$workspace" && HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$ambient_script" canonicalize \
  --cwd "$workspace" --source "$output_dir/dQw4w9WgXcQ.vtt" --source-url "$url" \
  --title 'Missing receipt' 2>&1)
missing_receipt_status=$?
set -e
test "$missing_receipt_status" -ne 0
printf '%s' "$missing_receipt_result" | grep -q 'requires --receipt'

cp "$output_dir/dQw4w9WgXcQ.vtt" "$test_root/caption-before-tamper.vtt"
printf '%s\n' tampered >>"$output_dir/dQw4w9WgXcQ.vtt"
set +e
tampered_result=$(cd "$workspace" && HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$ambient_script" canonicalize \
  --cwd "$workspace" --source "$output_dir/dQw4w9WgXcQ.vtt" --source-url "$url" \
  --title 'Tampered receipt' --receipt "$first_receipt_id" 2>&1)
tampered_status=$?
set -e
test "$tampered_status" -ne 0
printf '%s' "$tampered_result" | grep -q 'hash mismatch'
mv "$test_root/caption-before-tamper.vtt" "$output_dir/dQw4w9WgXcQ.vtt"
printf '%s\n' '{"schema_version":99}' >"$first_receipt"
set +e
malformed_result=$(cd "$workspace" && HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$ambient_script" canonicalize \
  --cwd "$workspace" --source "$output_dir/dQw4w9WgXcQ.vtt" --source-url "$url" \
  --title 'Malformed receipt' --receipt "$first_receipt_id" 2>&1)
malformed_status=$?
set -e
test "$malformed_status" -ne 0
printf '%s' "$malformed_result" | grep -q 'caption receipt rejected'
cp "$receipt_backup" "$first_receipt"
rm "$first_receipt"
set +e
absent_receipt_result=$(cd "$workspace" && HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$ambient_script" canonicalize \
  --cwd "$workspace" --source "$output_dir/dQw4w9WgXcQ.vtt" --source-url "$url" \
  --title 'Absent receipt' --receipt "$first_receipt_id" 2>&1)
absent_receipt_status=$?
set -e
test "$absent_receipt_status" -ne 0
printf '%s' "$absent_receipt_result" | grep -q 'caption receipt rejected'
cp "$receipt_backup" "$first_receipt"

symlink_success_workspace="$test_root/symlink-success-workspace"
mkdir -p "$symlink_success_workspace/.wiki/raw" "$symlink_success_workspace/.wiki/wiki" "$symlink_success_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$symlink_success_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$symlink_success_workspace/.wiki/_index.md"
symlink_success_output="$symlink_success_workspace/.wiki/inbox/youtube"
symlink_success_sentinel="$test_root/success-sentinel.txt"
printf '%s\n' sentinel >"$symlink_success_sentinel"
set +e
symlink_success_result=$(cd "$symlink_success_workspace" && FAKE_YTDLP_SUCCESS_SYMLINK=1 FAKE_SENTINEL="$symlink_success_sentinel" "$launcher" "$script" captions "$url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$symlink_success_output" 2>&1)
symlink_success_status=$?
set -e
test "$symlink_success_status" -eq 1
printf '%s' "$symlink_success_result" | grep -q 'caption-symlink'
test ! -L "$symlink_success_output/dQw4w9WgXcQ.vtt"
test -f "$symlink_success_sentinel"
grep -q '^sentinel$' "$symlink_success_sentinel"

concurrent_workspace="$test_root/concurrent-workspace"
mkdir -p "$concurrent_workspace/.wiki/raw" "$concurrent_workspace/.wiki/wiki" "$concurrent_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$concurrent_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$concurrent_workspace/.wiki/_index.md"
concurrent_output="$concurrent_workspace/.wiki/inbox/youtube"
concurrent_one="$test_root/concurrent-one.json"
concurrent_two="$test_root/concurrent-two.json"
(cd "$concurrent_workspace" && FAKE_YTDLP_DELAY=0.3 "$launcher" "$script" captions "$url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$concurrent_output" >"$concurrent_one") &
concurrent_pid_one=$!
(cd "$concurrent_workspace" && FAKE_YTDLP_DELAY=0.3 "$launcher" "$script" captions "$url" --attempts 1 --backoff 0 --timeout 5 --output-dir "$concurrent_output" >"$concurrent_two") &
concurrent_pid_two=$!
wait "$concurrent_pid_one"
wait "$concurrent_pid_two"
python3 - "$concurrent_one" "$concurrent_two" <<'PY'
import json
import sys

statuses = [json.load(open(path))['status'] for path in sys.argv[1:]]
assert sorted(statuses) == ['ok', 'stale-captions-ignored'], statuses
PY
test "$(find "$concurrent_workspace/.wiki/.sessions/wiki-agent-system/youtube-receipts" -type f -name '*.json' | wc -l | tr -d ' ')" -eq 1

tampered_queue_workspace="$test_root/tampered-queue-workspace"
mkdir -p "$tampered_queue_workspace/.wiki/raw" "$tampered_queue_workspace/.wiki/wiki" "$tampered_queue_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$tampered_queue_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$tampered_queue_workspace/.wiki/_index.md"
tampered_queue=$("$launcher" "$script" queue --workspace "$tampered_queue_workspace" --session-id tampered --turn-id one "$url")
tampered_queue_id=$(printf '%s' "$tampered_queue" | python3 -c 'import json,sys; print(json.load(sys.stdin)["queue_id"])')
"$launcher" "$script" drain --workspace "$tampered_queue_workspace" --queue-id "$tampered_queue_id" --deadline 10 --limit 1 >/dev/null
printf '%s\n' tampered >>"$tampered_queue_workspace/.wiki/inbox/youtube/dQw4w9WgXcQ.vtt"
tampered_snapshot=$("$launcher" "$script" queue --workspace "$tampered_queue_workspace" --session-id tampered --turn-id one "$url")
python3 - "$tampered_snapshot" <<'PY'
import json
import sys

item = json.loads(sys.argv[1])["records"][0]
assert item["status"] == "ok"
assert item["files"] == []
assert item["receipt"] is None
assert item["evidence_eligible"] is False
assert item["transcript_eligible"] is False
PY

model="$test_root/ggml-base.bin"
printf '%s\n' fixture-model >"$model"
transcription_dir="$workspace/.wiki/inbox/youtube/transcriptions"
stt_url='https://youtu.be/tGJTzahuapo?si=ignored'
stt_video='tGJTzahuapo'
stt_queue=$($launcher "$script" queue --workspace "$workspace" --session-id stt --turn-id one "$stt_url")
stt_queue_id=$(printf '%s' "$stt_queue" | python3 -c 'import json,sys; print(json.load(sys.stdin)["queue_id"])')
stt_drain=$(FAKE_YTDLP_METADATA=1 FAKE_YTDLP_NO_CAPTIONS=1 "$launcher" "$script" drain --workspace "$workspace" --queue-id "$stt_queue_id" --deadline 10 --limit 1)
printf '%s' "$stt_drain" | grep -q '"no-captions": 1'
transcription=$(cd "$workspace" && "$launcher" "$script" transcribe "$stt_url" --workspace "$workspace" --queue-id "$stt_queue_id" --timeout 5 --model "$model" --output-dir "$transcription_dir")
printf '%s' "$transcription" | grep -q '"status": "machine-transcription"'
printf '%s' "$transcription" | grep -q '"provenance_class": "machine-transcription"'
printf '%s' "$transcription" | grep -q '"evidence_eligible": false'
printf '%s' "$transcription" | grep -q '"transcript_eligible": false'
test -f "$transcription_dir/$stt_video.machine-transcription.txt"
grep -q 'transkripsi mesin fixture' "$transcription_dir/$stt_video.machine-transcription.txt"
transcription_provenance="$transcription_dir/$stt_video.machine-transcription.json"
test -f "$transcription_provenance"
python3 - "$transcription_provenance" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["schema_version"] == 1
assert data["engine"] == "whisper-cli"
assert data["language"] == "id"
assert len(data["model_sha256"]) == 64
assert len(data["transcript_sha256"]) == 64
PY
printf '%s' "$transcription" | grep -q '"transcription":'
existing_transcription=$(cd "$workspace" && "$launcher" "$script" transcribe "$stt_url" --workspace "$workspace" --queue-id "$stt_queue_id" --timeout 5 --model "$model" --output-dir "$transcription_dir")
printf '%s' "$existing_transcription" | grep -q '"status": "machine-transcription-existing"'
printf '%s' "$existing_transcription" | grep -q '"files": \[\]'

failed_transcription_dir="$workspace/.wiki/inbox/youtube/transcription-failed"
set +e
failed_transcription=$(cd "$workspace" && FAKE_WHISPER_FAIL=1 "$launcher" "$script" transcribe "$stt_url" --workspace "$workspace" --queue-id "$stt_queue_id" --timeout 5 --model "$model" --output-dir "$failed_transcription_dir" 2>&1)
status=$?
set -e
test "$status" -eq 1
printf '%s' "$failed_transcription" | grep -q '"status": "error"'
test ! -e "$failed_transcription_dir/$stt_video.machine-transcription.txt"
test ! -e "$failed_transcription_dir/$stt_video.machine-transcription.json"

set +e
pending_queue=$($launcher "$script" queue --workspace "$workspace" --session-id stt --turn-id pending "$stt_url")
pending_queue_id=$(printf '%s' "$pending_queue" | python3 -c 'import json,sys; print(json.load(sys.stdin)["queue_id"])')
premature_transcription=$(cd "$workspace" && "$launcher" "$script" transcribe "$stt_url" --workspace "$workspace" --queue-id "$pending_queue_id" --timeout 5 --model "$model" --output-dir "$workspace/.wiki/inbox/youtube/premature" 2>&1)
status=$?
set -e
test "$status" -eq 2
printf '%s' "$premature_transcription" | grep -q 'terminal caption miss'
test ! -e "$workspace/.wiki/inbox/youtube/premature"

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
metadata_queue_context=$(printf '%s' "$metadata_queue_output" | context_only)
printf '%s' "$metadata_queue_context" | grep -q 'Knowledge ready for ordinary use'
printf '%s' "$metadata_queue_context" | grep -q 'basis=source metadata'
printf '%s' "$metadata_queue_context" | grep -q 'source fact'
! printf '%s' "$metadata_queue_context" | grep -Eq 'status=no-captions|caption_evidence=|transcript=not-available|receipt=|evidence_status='
metadata_queue_record=$(find "$metadata_queue_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$metadata_queue_record" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert len(data["items"]) == 1
item = data["items"][0]
assert item["status"] == "no-captions"
assert item["caption_state"] == "no-captions"
assert item["metadata_state"] == "acquired"
assert item["url"] == "https://www.youtube.com/watch?v=tGJTzahuapo"
assert item["provenance_class"] == "metadata"
assert item["evidence_eligible"] is True
assert item["transcript_eligible"] is False
assert item["files"] == []
assert item["metadata"]["title"] == "Fixture metadata"
assert "tracking-query" not in json.dumps(item)
assert all(attempt["route"] in {"caption", "metadata"} for attempt in item["attempts"])
PY

foreground_retry_workspace="$test_root/foreground-retry-workspace"
mkdir -p "$foreground_retry_workspace/.wiki/raw" "$foreground_retry_workspace/.wiki/wiki" "$foreground_retry_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$foreground_retry_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$foreground_retry_workspace/.wiki/_index.md"
foreground_retry_prompt='Riset video https://youtu.be/S78sl3d8D1I'
foreground_retry_log="$test_root/foreground-retry-fetches.log"
foreground_retry_marker="$test_root/foreground-retry-once"
foreground_retry_output=$(printf '%s' "{\"cwd\":\"$foreground_retry_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"foreground-retry\",\"turn_id\":\"one\",\"prompt\":\"$foreground_retry_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_FAIL_ONCE_FILE="$foreground_retry_marker" FAKE_YTDLP_LOG="$foreground_retry_log" "$launcher" "$hook")
foreground_retry_context=$(printf '%s' "$foreground_retry_output" | context_only)
printf '%s' "$foreground_retry_context" | grep -q 'Knowledge ready for ordinary use'
printf '%s' "$foreground_retry_context" | grep -q 'basis=caption material'
! printf '%s' "$foreground_retry_context" | grep -Eq 'status=ok|caption_evidence=|receipt=|evidence_status='
foreground_retry_record=$(find "$foreground_retry_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
foreground_retry_controller=$(find "$foreground_retry_workspace/.wiki/.sessions/wiki-agent-system/foreground-loops" -type f -name '*.json' -print)
python3 - "$foreground_retry_record" "$foreground_retry_controller" <<'PY'
import json
import sys

queue = json.load(open(sys.argv[1]))
item = queue["items"][0]
assert item["status"] == "ok"
assert item["attempt"] == 2
assert item["retry_count"] == 1
controller = json.load(open(sys.argv[2]))
assert controller["state"] == "evidence-ready"
assert controller["revision"] == 2
PY
test "$(wc -l <"$foreground_retry_log" | tr -d ' ')" -eq 3

metadata_retry_workspace="$test_root/metadata-retry-workspace"
mkdir -p "$metadata_retry_workspace/.wiki/raw" "$metadata_retry_workspace/.wiki/wiki" "$metadata_retry_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$metadata_retry_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$metadata_retry_workspace/.wiki/_index.md"
metadata_retry_url='https://youtu.be/tGJTzahuapo?si=retry-metadata'
metadata_retry_queue=$("$launcher" "$script" queue --workspace "$metadata_retry_workspace" --session-id metadata-retry --turn-id one "$metadata_retry_url")
metadata_retry_id=$(printf '%s' "$metadata_retry_queue" | python3 -c 'import json,sys; print(json.load(sys.stdin)["queue_id"])')
metadata_retry_first=$(FAKE_YTDLP_FAIL_CAPTION=1 FAKE_YTDLP_METADATA=1 "$launcher" "$script" drain --workspace "$metadata_retry_workspace" --queue-id "$metadata_retry_id" --deadline 10 --limit 1)
printf '%s' "$metadata_retry_first" | grep -q '"status": "retryable"'
metadata_retry_record=$(find "$metadata_retry_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
metadata_retry_backup="$test_root/metadata-retry-old.json"
python3 - "$metadata_retry_record" "$metadata_retry_backup" <<'PY'
import json
import sys
from pathlib import Path

path, backup = map(Path, sys.argv[1:])
data = json.loads(path.read_text())
item = data["items"][0]
assert item["status"] == "retryable"
assert item["metadata_state"] == "acquired"
old_metadata = item["metadata"]
item["next_retry_at"] = 0
path.write_text(json.dumps(data, sort_keys=True) + "\n")
backup.write_text(json.dumps(old_metadata, sort_keys=True))
PY
metadata_retry_second=$("$launcher" "$script" drain --workspace "$metadata_retry_workspace" --queue-id "$metadata_retry_id" --deadline 10 --limit 1)
printf '%s' "$metadata_retry_second" | grep -q '"status": "ok"'
python3 - "$metadata_retry_record" "$metadata_retry_backup" <<'PY'
import json
import sys
from pathlib import Path

path, backup = map(Path, sys.argv[1:])
item = json.loads(path.read_text())["items"][0]
old_metadata = json.loads(backup.read_text())
assert item["status"] == "ok"
assert item["caption_state"] == "verified"
assert item["metadata_state"] == "acquired"
assert item["metadata"] == old_metadata
assert item["metadata"]["title"] == "Fixture metadata"
assert item["receipt"]["provenance_class"] == "caption"
assert item["receipt"]["files"]
PY

stale_context_workspace="$test_root/stale-context-workspace"
mkdir -p "$stale_context_workspace/.wiki/raw" "$stale_context_workspace/.wiki/wiki" "$stale_context_workspace/.wiki/inbox/youtube"
printf '%s\n' '# Workspace Wiki' >"$stale_context_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$stale_context_workspace/.wiki/_index.md"
printf '%s\n' 'WEBVTT' '' '00:00.000 --> 00:01.000' 'stale fixture caption' >"$stale_context_workspace/.wiki/inbox/youtube/dQw4w9WgXcQ.vtt"
stale_context_prompt='Riset video https://youtu.be/dQw4w9WgXcQ'
stale_context_output=$(printf '%s' "{\"cwd\":\"$stale_context_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"stale-context\",\"turn_id\":\"one\",\"prompt\":\"$stale_context_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
stale_context=$(printf '%s' "$stale_context_output" | context_only)
printf '%s' "$stale_context" | grep -q 'Source material is unavailable'
! printf '%s' "$stale_context" | grep -q 'Knowledge ready for ordinary use'
! printf '%s' "$stale_context" | grep -Eq 'status=no-captions|caption_evidence=|transcript=not-available|receipt=|evidence_status='

unreceipted_workspace="$test_root/unreceipted-workspace"
mkdir -p "$unreceipted_workspace/.wiki/raw" "$unreceipted_workspace/.wiki/wiki" "$unreceipted_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$unreceipted_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$unreceipted_workspace/.wiki/_index.md"
unreceipted_url='https://www.youtube.com/watch?v=62qCljKilH8'
unreceipted_queue=$($launcher "$script" queue --workspace "$unreceipted_workspace" --session-id unreceipted --turn-id one "$unreceipted_url")
unreceipted_queue_id=$(printf '%s' "$unreceipted_queue" | python3 -c 'import json,sys; print(json.load(sys.stdin)["queue_id"])')
unreceipted_record=$(find "$unreceipted_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$unreceipted_record" "$unreceipted_workspace" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

queue_path, workspace = map(Path, sys.argv[1:])
data = json.loads(queue_path.read_text())
item = data["items"][0]
caption = workspace / ".wiki" / "inbox" / "youtube" / "62qCljKilH8.vtt"
caption.parent.mkdir(parents=True, exist_ok=True)
caption.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nunreceipted fixture caption\n")
now = datetime.now(timezone.utc).isoformat()
item.update({
    "status": "ok",
    "attempt": 1,
    "terminal_at": now,
    "helper_status": "legacy-unreceipted",
    "files": [caption.name],
    "provenance_class": "caption",
    "evidence_eligible": True,
    "transcript_eligible": True,
    "evidence_reason": "caption-receipt-missing",
    "attempts": [],
    "metadata": None,
    "receipt": None,
    "caption_state": "verified",
    "metadata_state": "absent",
    "next_retry_at": None,
})
data["updated_at"] = now
queue_path.write_text(json.dumps(data, sort_keys=True) + "\n")
PY
unreceipted_output=$(printf '%s' "{\"cwd\":\"$unreceipted_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"unreceipted\",\"turn_id\":\"one\",\"prompt\":\"Research and synthesize video $unreceipted_url\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
unreceipted_context=$(printf '%s' "$unreceipted_output" | context_only)
printf '%s' "$unreceipted_context" | grep -q 'Knowledge ready for ordinary use'
printf '%s' "$unreceipted_context" | grep -q 'basis=caption material'
! printf '%s' "$unreceipted_context" | grep -Eq 'status=ok|stale_caption_files=|caption_evidence=|receipt=|evidence_status=|confidence'
unreceipted_stop=$(printf '%s' "{\"cwd\":\"$unreceipted_workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"unreceipted\",\"turn_id\":\"one\",\"last_assistant_message\":\"Unverified caption material was used with provenance and confidence.\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
! printf '%s' "$unreceipted_stop" | grep -q '"decision": "block"'
test "$(find "$unreceipted_workspace/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 1

failure_blocked_workspace="$test_root/failure-blocked-workspace"
mkdir -p "$failure_blocked_workspace/.wiki/raw" "$failure_blocked_workspace/.wiki/wiki" "$failure_blocked_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$failure_blocked_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$failure_blocked_workspace/.wiki/_index.md"
blocked_url='https://www.youtube.com/watch?v=S78sl3d8D1I'
failure_url='https://www.youtube.com/watch?v=DEG-k0r9C2E'
failure_blocked_queue=$($launcher "$script" queue --workspace "$failure_blocked_workspace" --session-id failure-blocked --turn-id one "$blocked_url" "$failure_url")
failure_blocked_record=$(find "$failure_blocked_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$failure_blocked_record" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())
now = datetime.now(timezone.utc).isoformat()
for item in data["items"]:
    blocked = item["video_id"] == "S78sl3d8D1I"
    item.update({
        "status": "blocked" if blocked else "error",
        "attempt": 1,
        "terminal_at": now,
        "helper_status": "blocked" if blocked else "error",
        "error_class": "access-boundary" if blocked else "caption-route-failed",
        "files": [],
        "provenance_class": "none",
        "evidence_eligible": False,
        "transcript_eligible": False,
        "evidence_reason": "access-boundary" if blocked else "caption-route-failed",
        "attempts": [],
        "metadata": None,
        "receipt": None,
        "caption_state": "blocked" if blocked else "exhausted",
        "metadata_state": "absent",
        "next_retry_at": None,
    })
data["updated_at"] = now
path.write_text(json.dumps(data, sort_keys=True) + "\n")
PY
failure_blocked_output=$(printf '%s' "{\"cwd\":\"$failure_blocked_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"failure-blocked\",\"turn_id\":\"one\",\"prompt\":\"Riset video $blocked_url dan $failure_url\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
failure_blocked_context=$(printf '%s' "$failure_blocked_output" | context_only)
test "$(printf '%s' "$failure_blocked_context" | grep -c 'Source material is unavailable')" -ge 2
! printf '%s' "$failure_blocked_context" | grep -Eq 'status=blocked|status=error|knowledge_readiness=|evidence_status=|receipt=|queue:|confidence'
failure_blocked_stop=$(printf '%s' "{\"cwd\":\"$failure_blocked_workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"failure-blocked\",\"turn_id\":\"one\",\"last_assistant_message\":\"Acquisition failed or was blocked; no source material was used.\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
! printf '%s' "$failure_blocked_stop" | grep -q '"decision": "block"'
if test -d "$failure_blocked_workspace/.wiki/inbox/autosave"; then
  test "$(find "$failure_blocked_workspace/.wiki/inbox/autosave" -type f -name 'session-*.md' | wc -l | tr -d ' ')" -eq 0
fi
test -z "$(find "$failure_blocked_workspace/.wiki/wiki" -type f -print -quit)"

hook_workspace="$test_root/hook-workspace"
hook_log="$test_root/hook-fetches.log"
mkdir -p "$hook_workspace/.wiki/raw" "$hook_workspace/.wiki/wiki" "$hook_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$hook_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$hook_workspace/.wiki/_index.md"
historical_prompt='Tolong riset dan buat knowledge base berbahasa Indonesia dari empat video YouTube berikut: https://www.youtube.com/watch?v=S78sl3d8D1I https://www.youtube.com/watch?v=DEG-k0r9C2E https://www.youtube.com/watch?v=tGJTzahuapo https://www.youtube.com/watch?v=62qCljKilH8'
hook_output=$(printf '%s' "{\"cwd\":\"$hook_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"youtube-hook\",\"turn_id\":\"one\",\"prompt\":\"$historical_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_LOG="$hook_log" "$launcher" "$hook")
hook_context=$(printf '%s' "$hook_output" | context_only)
printf '%s' "$hook_context" | grep -q 'Source material for the requested video'
printf '%s' "$hook_context" | grep -q 'Knowledge ready for ordinary use'
printf '%s' "$hook_context" | grep -q 'basis=caption material'
! printf '%s' "$hook_context" | grep -Eq 'status=ok|caption_evidence=|receipt_id=|caption_sha256=|terminal=|queue:|confidence'
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
assert data["schema_version"] == 3
assert len(data["items"]) == 4
assert all(item["status"] in {"pending", "ok", "no-captions", "retryable", "exhausted", "error", "blocked", "blocked-install"} for item in data["items"])
assert all(item["status"] == "ok" for item in data["items"])
assert all("session" not in json.dumps(item).lower() for item in data["items"])
assert not any(key in json.dumps(data) for key in ("PLUGIN_ROOT", "PLUGIN_DATA"))
PY
test "$(wc -l <"$hook_log" | tr -d ' ')" -eq 4
blocked_stop=$(printf '%s' "{\"cwd\":\"$hook_workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"youtube-hook\",\"turn_id\":\"one\",\"last_assistant_message\":\"Still collecting video evidence\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
! printf '%s' "$blocked_stop" | grep -q '"decision": "block"'
! printf '%s' "$blocked_stop" | grep -Eq 'queue_id|revision=|loop=|/wiki-agent-system|simulated yt-dlp failure|retry|verify|authority'
python3 - "$hook_workspace" "$queue_record" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

workspace, queue_path = map(Path, sys.argv[1:])
wiki = workspace / ".wiki"
data = json.loads(queue_path.read_text())
records = data["items"]
bindings = []
receipt_ids = []
hashes = []
for item in records:
    receipt = item["receipt"]
    receipt_ids.append(receipt["receipt_id"])
    for entry in receipt["files"]:
        value = f"{receipt['receipt_id']}/{entry['sha256']}"
        bindings.append(value)
        hashes.append(entry["sha256"])
body = "## Synthesis\n\nGrounded synthesis from the verified caption evidence.\n\n## Sources\n\n"
body += "\n".join(
    f"- {item['url']} receipt={item['receipt']['receipt_id']} hash={item['receipt']['files'][0]['sha256']}"
    for item in records
)
body += "\n\n## Quality\n\nClaim-to-receipt mapping, provenance, and quality validation passed.\n"
artifact = wiki / "wiki" / "youtube-knowledge.md"
artifact_path = "wiki/youtube-knowledge.md"
artifact.write_text("\n".join([
    "---",
    "type: youtube-knowledge",
    "schema: 1",
    "status: pending-curation",
    f"queue_id: {data['queue_id']}",
    f"artifact_path: {artifact_path}",
    f"artifact_sha256: {hashlib.sha256(body.encode()).hexdigest()}",
    f"receipt_ids: {';'.join(receipt_ids)}",
    f"receipt_hashes: {';'.join(bindings)}",
    f"source_urls: {';'.join(item['url'] for item in records)}",
    f"claim_evidence: {';'.join(bindings)}",
    "provenance_class: caption",
    "evidence_status: verified",
    "grounded: true",
    "quality_status: verified",
    "---",
    body,
]))
PY
final_stop=$(printf '%s' "{\"cwd\":\"$hook_workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"youtube-hook\",\"turn_id\":\"one\",\"last_assistant_message\":\"Grounded knowledge artifact finalized from caption receipts\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
! printf '%s' "$final_stop" | grep -q '"decision": "block"'
test "$(wc -l <"$hook_log" | tr -d ' ')" -eq 4
find "$hook_workspace/.wiki/.sessions/wiki-agent-system/foreground-loops" -type f -name '*.json' -print \
  | xargs -n1 grep -q '"state": "verified"'
python3 - "$queue_record" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert all(item["status"] == "ok" for item in data["items"])
assert all(item["attempt"] == 1 for item in data["items"])
assert all(item["provenance_class"] == "caption" for item in data["items"])
assert all(item["evidence_eligible"] is True for item in data["items"])
assert all(item["transcript_eligible"] is True for item in data["items"])
assert all(item["evidence_reason"] == "fresh-regular-vtt" for item in data["items"])
assert all(item["attempts"] and item["attempts"][0]["route"] == "caption" for item in data["items"])
assert all(item["receipt"]["provenance_class"] == "caption" for item in data["items"])
assert all(len(item["receipt"]["files"]) == 1 for item in data["items"])
assert not any(key in json.dumps(data) for key in ("PLUGIN_ROOT", "PLUGIN_DATA"))
PY

interrupt_workspace="$test_root/interrupt-workspace"
mkdir -p "$interrupt_workspace/.wiki/raw" "$interrupt_workspace/.wiki/wiki" "$interrupt_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$interrupt_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$interrupt_workspace/.wiki/_index.md"
interrupt_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ'
"$launcher" "$script" queue --workspace "$interrupt_workspace" --session-id interrupt --turn-id one "$interrupt_url" >/dev/null
interrupt_queue=$(find "$interrupt_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$hook" "$interrupt_workspace" <<'PY'
import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path

hook, workspace = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("preflight", hook)
module = importlib.util.module_from_spec(spec)
sys.stdin = io.StringIO(json.dumps({"cwd": str(workspace), "hook_event_name": "SessionStart"}))
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(module)
wiki = workspace / ".wiki"
queue_id = module.queue_id_for("interrupt", "one")
module.foreground_controller_directory(wiki)
path, _ = module.foreground_controller_paths(wiki, queue_id)
controller = module.new_foreground_controller(queue_id)
controller.update({"state": "retryable", "action": "caption-retry"})
module.atomic_queue_write(path, controller)
PY
interrupt_controller=$(find "$interrupt_workspace/.wiki/.sessions/wiki-agent-system/foreground-loops" -type f -name '*.json' -print)
python3 - "$interrupt_queue" "$interrupt_controller" "$interrupt_workspace" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

queue_path, controller_path, workspace = map(Path, sys.argv[1:])
data = json.loads(queue_path.read_text())
item = data["items"][0]
caption = workspace / ".wiki" / "inbox" / "youtube" / f"{item['video_id']}.vtt"
caption.parent.mkdir(parents=True, exist_ok=True)
caption.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\ninterrupted receipt\n")
receipt = {
    "schema_version": 1,
    "receipt_id": "c" * 32,
    "canonical_url": item["url"],
    "video_id": item["video_id"],
    "status": "ok",
    "provenance_class": "caption",
    "attempt": 1,
    "attempted_at": datetime.now(timezone.utc).isoformat(),
    "files": [{
        "path": f"inbox/youtube/{caption.name}",
        "sha256": hashlib.sha256(caption.read_bytes()).hexdigest(),
    }],
}
receipt_dir = workspace / ".wiki" / ".sessions" / "wiki-agent-system" / "youtube-receipts"
receipt_dir.mkdir(parents=True, exist_ok=True)
(receipt_dir / f"{receipt['receipt_id']}.json").write_text(json.dumps(receipt) + "\n")
item.update({
    "status": "running",
    "started_at": datetime.now(timezone.utc).isoformat(),
    "terminal_at": None,
    "lease_until": 4102444800,
    "error_class": None,
    "files": [caption.name],
    "receipt": receipt,
    "caption_state": "running",
    "metadata_state": "absent",
    "provenance_class": "none",
    "evidence_eligible": False,
    "transcript_eligible": False,
    "evidence_reason": "not-attempted",
})
data["updated_at"] = datetime.now(timezone.utc).isoformat()
queue_path.write_text(json.dumps(data, sort_keys=True) + "\n")
controller = json.loads(controller_path.read_text())
assert controller["state"] == "retryable"
assert controller["action"] == "caption-retry"
PY
printf '%s' "{\"cwd\":\"$interrupt_workspace\",\"hook_event_name\":\"Interrupt\",\"session_id\":\"interrupt\",\"turn_id\":\"one\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook" >/dev/null
python3 - "$interrupt_queue" "$interrupt_controller" <<'PY'
import json
import sys
from pathlib import Path

queue = json.loads(Path(sys.argv[1]).read_text())
item = queue["items"][0]
assert item["status"] == "pending"
assert item["error_class"] == "interrupted"
assert item["lease_until"] is None
controller = json.loads(Path(sys.argv[2]).read_text())
assert controller["state"] == "retryable"
assert "interrupted checkpoint preserved" in controller["reason"]
PY
interrupt_continuation=$(printf '%s' "{\"cwd\":\"$interrupt_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"interrupt\",\"turn_id\":\"one\",\"prompt\":\"Foreground continuation\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_FAIL=1 "$launcher" "$hook")
interrupt_continuation_context=$(printf '%s' "$interrupt_continuation" | context_only)
printf '%s' "$interrupt_continuation_context" | grep -q 'Knowledge ready for ordinary use'
printf '%s' "$interrupt_continuation_context" | grep -q 'basis=caption material'
! printf '%s' "$interrupt_continuation_context" | grep -Eq 'state=|status=|receipt=|evidence_status=|confidence'
test ! -e "$test_root/interrupt-fetches.log"
python3 - "$interrupt_queue" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
item = data["items"][0]
assert item["status"] == "ok"
assert item["receipt"]["receipt_id"] == "c" * 32
assert item["transcript_eligible"] is True
PY
test "$(find "$interrupt_workspace/.wiki/.sessions/wiki-agent-system/youtube-receipts" -type f -name '*.json' | wc -l | tr -d ' ')" -eq 1
python3 - "$interrupt_workspace" "$interrupt_queue" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

workspace, queue_path = map(Path, sys.argv[1:])
wiki = workspace / ".wiki"
data = json.loads(queue_path.read_text())
item = data["items"][0]
receipt = item["receipt"]
binding = f"{receipt['receipt_id']}/{receipt['files'][0]['sha256']}"
body = f"## Synthesis\n\nGrounded interrupted-work recovery for {item['url']}.\n\n## Sources\n\n- {item['url']} receipt={receipt['receipt_id']} hash={receipt['files'][0]['sha256']}\n\n## Quality\n\nReceipt reuse and provenance validation passed.\n"
artifact = wiki / "wiki" / "interrupt-knowledge.md"
artifact.write_text("\n".join([
    "---", "type: youtube-knowledge", "schema: 1", "status: pending-curation",
    f"queue_id: {data['queue_id']}", "artifact_path: wiki/interrupt-knowledge.md",
    f"artifact_sha256: {hashlib.sha256(body.encode()).hexdigest()}",
    f"receipt_ids: {receipt['receipt_id']}", f"receipt_hashes: {binding}",
    f"source_urls: {item['url']}", f"claim_evidence: {binding}",
    "provenance_class: caption", "evidence_status: verified", "grounded: true",
    "quality_status: verified", "---", body,
]))
PY
interrupt_final_stop=$(printf '%s' "{\"cwd\":\"$interrupt_workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"interrupt\",\"turn_id\":\"one\",\"last_assistant_message\":\"Grounded interrupted knowledge finalized\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
! printf '%s' "$interrupt_final_stop" | grep -q '"decision": "block"'

race_url_one='https://www.youtube.com/watch?v=S78sl3d8D1I'
race_url_two='https://www.youtube.com/watch?v=DEG-k0r9C2E'
slow_workspace="$test_root/slow-hook-workspace"
mkdir -p "$slow_workspace/.wiki/raw" "$slow_workspace/.wiki/wiki" "$slow_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$slow_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$slow_workspace/.wiki/_index.md"
slow_started=$(date +%s)
slow_output=$(printf '%s' "{\"cwd\":\"$slow_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"slow-hook\",\"turn_id\":\"one\",\"prompt\":\"$historical_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_CAPTION_DELAY=8 FAKE_YTDLP_METADATA_DELAY=10 FAKE_YTDLP_NO_CAPTIONS=1 "$launcher" "$hook")
slow_elapsed=$(( $(date +%s) - slow_started ))
test "$slow_elapsed" -lt 45
slow_context=$(printf '%s' "$slow_output" | context_only)
test "$(printf '%s' "$slow_context" | grep -c 'Source material is unavailable')" -ge 4
! printf '%s' "$slow_context" | grep -Eq 'status=retryable|terminal=|acquisition:|evidence_status=|queue:|confidence'
slow_record=$(find "$slow_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$slow_record" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert data["schema_version"] == 3
assert len(data["items"]) == 4
assert all(item["status"] in {"retryable", "pending"} for item in data["items"])
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
assert data["schema_version"] == 3
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
no_identity_first_context=$(printf '%s' "$no_identity_first" | context_only)
no_identity_second_context=$(printf '%s' "$no_identity_second" | context_only)
printf '%s' "$no_identity_first_context" | grep -q 'Source material is unavailable'
printf '%s' "$no_identity_second_context" | grep -q 'Source material is unavailable'
! printf '%s\n%s' "$no_identity_first_context" "$no_identity_second_context" | grep -Eq 'acquisition=|transcript claim|evidence_status=|receipt=|queue:|confidence'
test ! -e "$no_identity_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues"
test ! -e "$no_identity_workspace/.wiki/inbox/youtube/S78sl3d8D1I.vtt"
test ! -e "$no_identity_log"

repeat_output=$(printf '%s' "{\"cwd\":\"$hook_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"youtube-hook\",\"turn_id\":\"one\",\"prompt\":\"$historical_prompt\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" FAKE_YTDLP_LOG="$hook_log" "$launcher" "$hook")
repeat_context=$(printf '%s' "$repeat_output" | context_only)
test "$(printf '%s' "$repeat_context" | grep -c 'Knowledge ready for ordinary use')" -eq 4
printf '%s' "$repeat_context" | grep -q 'basis=caption material'
! printf '%s' "$repeat_context" | grep -Eq 'terminal=|pending=|status=|receipt=|evidence_status=|confidence'
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
test "$(find "$race_workspace/.wiki/.sessions/wiki-agent-system/youtube-receipts" -type f -name '*.json' | wc -l | tr -d ' ')" -eq 2

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
pending_context=$(printf '%s' "$pending_output" | context_only)
test "$(printf '%s' "$pending_context" | grep -c 'Knowledge ready for ordinary use')" -eq 5
printf '%s' "$pending_context" | grep -q 'basis=caption material'
! printf '%s' "$pending_context" | grep -Eq 'terminal=|pending=|status=|receipt=|evidence_status=|confidence'
pending_record=$(find "$pending_workspace/.wiki/.sessions/wiki-agent-system/youtube-queues" -type f -name '*.json' -print)
python3 - "$pending_record" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert len(data["items"]) == 5
assert all(item["status"] == "ok" for item in data["items"])
PY
python3 - "$pending_workspace" "$pending_record" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

workspace, queue_path = map(Path, sys.argv[1:])
wiki = workspace / ".wiki"
data = json.loads(queue_path.read_text())
records = data["items"]
bindings = [f"{item['receipt']['receipt_id']}/{item['receipt']['files'][0]['sha256']}" for item in records]
body = "## Synthesis\n\nGrounded pending-queue synthesis.\n\n## Sources\n\n" + "\n".join(
    f"- {item['url']} receipt={item['receipt']['receipt_id']} hash={item['receipt']['files'][0]['sha256']}"
    for item in records
) + "\n\n## Quality\n\nAll claims passed receipt-bound provenance validation.\n"
artifact = wiki / "wiki" / "pending-knowledge.md"
artifact.write_text("\n".join([
    "---", "type: youtube-knowledge", "schema: 1", "status: pending-curation",
    f"queue_id: {data['queue_id']}", "artifact_path: wiki/pending-knowledge.md",
    f"artifact_sha256: {hashlib.sha256(body.encode()).hexdigest()}",
    f"receipt_ids: {';'.join(item['receipt']['receipt_id'] for item in records)}",
    f"receipt_hashes: {';'.join(bindings)}",
    f"source_urls: {';'.join(item['url'] for item in records)}",
    f"claim_evidence: {';'.join(bindings)}", "provenance_class: caption",
    "evidence_status: verified", "grounded: true", "quality_status: verified", "---", body,
]))
PY
pending_final_stop=$(printf '%s' "{\"cwd\":\"$pending_workspace\",\"hook_event_name\":\"Stop\",\"session_id\":\"pending\",\"turn_id\":\"one\",\"last_assistant_message\":\"Grounded pending knowledge finalized\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
! printf '%s' "$pending_final_stop" | grep -q '"decision": "block"'

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
grep -Fq 'durable foreground controller' "$policy"
for document in "$policy" "$ambient" "$workspace_skill"; do
  ! grep -Fq '$PLUGIN_ROOT' "$document"
  ! grep -Fq '$PLUGIN_DATA' "$document"
done

grep -q '"timeout": 45' "$root/plugins/wiki-preflight/hooks/hooks.json"

printf '%s\n' 'youtube fallback test passed'
