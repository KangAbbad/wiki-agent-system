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
import os
from pathlib import Path
import sys

if "--version" in sys.argv:
    print("2026.08.19")
    raise SystemExit(0)
if os.environ.get("FAKE_YTDLP_FAIL") == "1":
    template = Path(sys.argv[sys.argv.index("--output") + 1])
    target = Path(str(template).replace("%(id)s", "dQw4w9WgXcQ").replace("%(ext)s", "vtt"))
    if target.exists():
        target.write_text(target.read_text())
    residue = target.with_name("dQw4w9WgXcQ.failed.vtt")
    residue.write_text("WEBVTT\n\nfailed attempt\n")
    if os.environ.get("FAKE_YTDLP_SYMLINK") == "1":
        residue.with_name("dQw4w9WgXcQ.link.vtt").symlink_to(os.environ["FAKE_SENTINEL"])
    print("simulated yt-dlp failure", file=sys.stderr)
    raise SystemExit(1)

template = Path(sys.argv[sys.argv.index("--output") + 1])
target = Path(str(template).replace("%(id)s", "dQw4w9WgXcQ").replace("%(ext)s", "vtt"))
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

hook_workspace="$test_root/hook-workspace"
mkdir -p "$hook_workspace/.wiki/raw" "$hook_workspace/.wiki/wiki" "$hook_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$hook_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$hook_workspace/.wiki/_index.md"
hook_output=$(printf '%s' "{\"cwd\":\"$hook_workspace\",\"hook_event_name\":\"UserPromptSubmit\",\"session_id\":\"youtube-hook\",\"turn_id\":\"one\",\"prompt\":\"Riset video $url\"}" \
  | HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" "$launcher" "$hook")
printf '%s' "$hook_output" | grep -q 'YouTube automatic fallback preflight'
printf '%s' "$hook_output" | grep -q 'status=ok'
test -f "$hook_workspace/.wiki/inbox/youtube/dQw4w9WgXcQ.vtt"

grep -q '"timeout": 45' "$root/plugins/wiki-preflight/hooks/hooks.json"

printf '%s\n' 'youtube fallback test passed'
