#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
test_plugin="$test_root/plugin"
cp -R "$root/plugins/wiki-preflight" "$test_plugin"
launcher="$test_plugin/hooks/launcher.sh"
ambient="$test_plugin/scripts/wiki_ambient.py"

make_wiki() {
  workspace=$1
  mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
  printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"
}

legacy_record() {
  path=$1
  title=$2
  source_url=$3
  canonical_uri=$4
  body=$5
  schema=${6:-1}
  digest_override=${7:-}
  digest=$(printf '%s\n' "$body" | shasum -a 256 | awk '{print $1}')
  test -z "$digest_override" || digest=$digest_override
  printf '%s\n' \
    '---' \
    "schema: $schema" \
    "title: \"$title\"" \
    "source_url: \"$source_url\"" \
    'type: raw-source' \
    'retrieved: 2026-09-20' \
    'retrieved_at: 2026-09-20T12:00:00+00:00' \
    "content_sha256: $digest" \
    'provenance_class: web-extraction' \
    'evidence_status: unverified' \
    'evidence_eligible: true' \
    'transcript_eligible: false' \
    "evidence_uri: \"$canonical_uri\"" \
    'status: canonical' \
    'supersedes: null' \
    'valid_until: null' \
    '---' \
    "# $title" \
    '' \
    "$body" >"$path"
}

assert_count() {
  expected=$1
  printf '%s' "$2" | python3 -c 'import json, sys; assert json.load(sys.stdin)["migrated"] == int(sys.argv[1])' "$expected"
}

expect_rejected() {
  workspace=$1
  error_class=$2
  set +e
  output=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" \
    "$launcher" "$ambient" migrate-evidence --cwd "$workspace" 2>&1)
  status=$?
  set -e
  test "$status" -eq 2
  printf '%s' "$output" | grep -q "\"error_class\": \"$error_class\""
}

success_workspace="$test_root/success"
make_wiki "$success_workspace"
success_body='Legacy evidence body.'
success_digest=$(printf '%s\n' "$success_body" | shasum -a 256 | awk '{print $1}')
success_uri="wiki://workspace/evidence/$success_digest"
success_legacy="$success_workspace/.wiki/raw/legacy.md"
legacy_record "$success_legacy" 'Legacy web' 'https://example.test/legacy' "$success_uri" "$success_body"
mkdir -p "$test_root/config/llm-wiki"
printf '%s\n' '{"schema_version":4,"sentinel":"unchanged"}' >"$test_root/config/llm-wiki/wiki-agent-system.json"
cp "$test_root/config/llm-wiki/wiki-agent-system.json" "$test_root/config.before"

first=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" \
  "$launcher" "$ambient" migrate-evidence --cwd "$success_workspace")
assert_count 1 "$first"
test ! -e "$success_legacy"
success_target=$(find "$success_workspace/.wiki/raw/articles" -maxdepth 1 -type f -name 'legacy-web-*.md' -print)
test -n "$success_target"
python3 - "$ambient" "$success_target" "$success_digest" "$success_uri" "$success_workspace/.wiki" <<'PY'
import hashlib
import sys
from pathlib import Path

ambient, path, digest, uri, wiki = sys.argv[1:]
sys.path.insert(0, str(Path(ambient).parent))
import wiki_ambient

text = Path(path).read_text(encoding="utf-8")
header, body = text.split("\n---\n", 1)
fields = {}
for line in header.splitlines()[1:]:
    key, value = line.split(":", 1)
    fields[key] = value.strip().strip('"')
assert fields["type"] == "articles"
assert fields["source"] == "https://example.test/legacy"
assert fields["ingested"] == "2026-09-20"
assert fields["summary"] == "Wiki Preflight web-extraction evidence; lifecycle=unverified."
assert fields["tags"] == "[wiki-preflight, evidence, web-extraction]"
assert fields["content_sha256"] == digest
assert fields["canonical_uri"] == uri
assert fields["status"] == "canonical"
assert fields["evidence_status"] == "unverified"
assert fields["evidence_eligible"] == "true"
assert fields["transcript_eligible"] == "false"
assert body == "\n# Legacy web\n\nLegacy evidence body.\n"
assert hashlib.sha256(b"Legacy evidence body.\n").hexdigest() == digest
assert wiki_ambient.resolve_local_canonical_record(Path(wiki).resolve(), uri) == Path(path).resolve()
PY
cmp "$test_root/config.before" "$test_root/config/llm-wiki/wiki-agent-system.json"
test ! -e "$success_workspace/.wiki/.sessions"
second=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" \
  "$launcher" "$ambient" migrate-evidence --cwd "$success_workspace")
assert_count 0 "$second"
test -f "$success_target"
cmp "$test_root/config.before" "$test_root/config/llm-wiki/wiki-agent-system.json"

ordinary_workspace="$test_root/ordinary"
make_wiki "$ordinary_workspace"
ordinary="$ordinary_workspace/.wiki/raw/user-note.md"
printf '%s\n' '# User-created raw note' >"$ordinary"
ordinary_before=$(shasum -a 256 "$ordinary" | awk '{print $1}')
ordinary_result=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" \
  "$launcher" "$ambient" migrate-evidence --cwd "$ordinary_workspace")
assert_count 0 "$ordinary_result"
test "$(shasum -a 256 "$ordinary" | awk '{print $1}')" = "$ordinary_before"
test ! -e "$ordinary_workspace/.wiki/raw/articles"

future_workspace="$test_root/future"
make_wiki "$future_workspace"
future="$future_workspace/.wiki/raw/future.md"
legacy_record "$future" 'Future schema' 'https://example.test/future' "$success_uri" 'Future body.' 99
future_before=$(shasum -a 256 "$future" | awk '{print $1}')
expect_rejected "$future_workspace" future-schema
test "$(shasum -a 256 "$future" | awk '{print $1}')" = "$future_before"
test ! -e "$future_workspace/.wiki/raw/articles"

malformed_workspace="$test_root/malformed"
make_wiki "$malformed_workspace"
malformed="$malformed_workspace/.wiki/raw/malformed.md"
printf '%s\n' '---' 'type: raw-source' 'status: canonical' 'content_sha256: broken' >"$malformed"
malformed_before=$(shasum -a 256 "$malformed" | awk '{print $1}')
expect_rejected "$malformed_workspace" frontmatter-invalid
test "$(shasum -a 256 "$malformed" | awk '{print $1}')" = "$malformed_before"
test ! -e "$malformed_workspace/.wiki/raw/articles"

hash_workspace="$test_root/hash"
make_wiki "$hash_workspace"
hash_record="$hash_workspace/.wiki/raw/hash.md"
legacy_record "$hash_record" 'Hash mismatch' 'https://example.test/hash' "$success_uri" 'Hash body.' 1 \
  '0000000000000000000000000000000000000000000000000000000000000000'
hash_before=$(shasum -a 256 "$hash_record" | awk '{print $1}')
expect_rejected "$hash_workspace" content-hash-mismatch
test "$(shasum -a 256 "$hash_record" | awk '{print $1}')" = "$hash_before"
test ! -e "$hash_workspace/.wiki/raw/articles"

caption_workspace="$test_root/caption-tamper"
make_wiki "$caption_workspace"
caption_file="$caption_workspace/.wiki/inbox/youtube/dQw4w9WgXcQ.vtt"
caption_receipt_dir="$caption_workspace/.wiki/.sessions/wiki-agent-system/youtube-receipts"
mkdir -p "$(dirname "$caption_file")" "$caption_receipt_dir"
caption_body=$(printf 'WEBVTT\n\n00:00.000 --> 00:01.000\nReceipt caption')
printf '%s\n' "$caption_body" >"$caption_file"
caption_digest=$(shasum -a 256 "$caption_file" | awk '{print $1}')
caption_receipt_id=0123456789abcdef0123456789abcdef
caption_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ'
caption_uri="wiki://workspace/capture/receipt-test/source-$caption_digest"
caption_record="$caption_workspace/.wiki/raw/caption.md"
printf '%s\n' \
  '---' \
  'schema: 1' \
  'title: "Receipt caption"' \
  "source_url: \"$caption_url\"" \
  'type: raw-source' \
  'retrieval_method: transcript-api' \
  'retrieved: 2026-09-20' \
  'retrieved_at: 2026-09-20T12:00:00+00:00' \
  "content_sha256: $caption_digest" \
  'provenance_class: caption' \
  'evidence_status: verified' \
  'evidence_eligible: true' \
  'transcript_eligible: true' \
  "receipt_id: $caption_receipt_id" \
  "transcript_sha256: $caption_digest" \
  "canonical_uri: \"$caption_uri\"" \
  'status: canonical' \
  'supersedes: null' \
  'valid_until: null' \
  '---' \
  '# Receipt caption' \
  '' \
  "$caption_body" >"$caption_record"
printf '%s\n' \
  '{' \
  '  "schema_version": 1,' \
  "  \"receipt_id\": \"$caption_receipt_id\"," \
  "  \"canonical_url\": \"$caption_url\"," \
  '  "video_id": "dQw4w9WgXcQ",' \
  '  "status": "ok",' \
  '  "provenance_class": "caption",' \
  '  "attempt": 1,' \
  '  "attempted_at": "2026-09-20T12:00:00+00:00",' \
  '  "files": [' \
  "    {\"path\": \"inbox/youtube/dQw4w9WgXcQ.vtt\", \"sha256\": \"$caption_digest\"}" \
  '  ]' \
  '}' >"$caption_receipt_dir/$caption_receipt_id.json"
caption_before=$(shasum -a 256 "$caption_record" | awk '{print $1}')
printf '%s\n' \
  '{' \
  "  \"receipt_id\": \"$caption_receipt_id\"," \
  "  \"canonical_url\": \"$caption_url\"," \
  '  "video_id": "dQw4w9WgXcQ",' \
  '  "status": "ok",' \
  '  "provenance_class": "caption",' \
  '  "attempt": 1,' \
  '  "attempted_at": "2026-09-20T12:00:00+00:00",' \
  '  "files": [' \
  '    {"path": "inbox/youtube/dQw4w9WgXcQ.vtt", "sha256": "0000000000000000000000000000000000000000000000000000000000000000"}' \
  '  ]' \
  '}' >"$caption_receipt_dir/$caption_receipt_id.json"
caption_receipt_before=$(shasum -a 256 "$caption_receipt_dir/$caption_receipt_id.json" | awk '{print $1}')
expect_rejected "$caption_workspace" receipt-invalid
test "$(shasum -a 256 "$caption_record" | awk '{print $1}')" = "$caption_before"
test "$(shasum -a 256 "$caption_receipt_dir/$caption_receipt_id.json" | awk '{print $1}')" = "$caption_receipt_before"
test ! -e "$caption_workspace/.wiki/raw/articles"

symlink_workspace="$test_root/symlink"
make_wiki "$symlink_workspace"
sentinel="$test_root/sentinel"
printf '%s\n' 'do not replace' >"$sentinel"
ln -s "$sentinel" "$symlink_workspace/.wiki/raw/linked.md"
expect_rejected "$symlink_workspace" symlink-source
test -L "$symlink_workspace/.wiki/raw/linked.md"
test ! -e "$symlink_workspace/.wiki/raw/articles"

duplicate_workspace="$test_root/duplicate"
make_wiki "$duplicate_workspace"
duplicate_uri="wiki://workspace/evidence/$(python3 -c 'print("a" * 64)')"
duplicate_one="$duplicate_workspace/.wiki/raw/one.md"
duplicate_two="$duplicate_workspace/.wiki/raw/two.md"
legacy_record "$duplicate_one" 'Duplicate one' 'https://example.test/one' "$duplicate_uri" 'One body.'
legacy_record "$duplicate_two" 'Duplicate two' 'https://example.test/two' "$duplicate_uri" 'Two body.'
duplicate_one_before=$(shasum -a 256 "$duplicate_one" | awk '{print $1}')
duplicate_two_before=$(shasum -a 256 "$duplicate_two" | awk '{print $1}')
expect_rejected "$duplicate_workspace" duplicate-canonical-uri
test "$(shasum -a 256 "$duplicate_one" | awk '{print $1}')" = "$duplicate_one_before"
test "$(shasum -a 256 "$duplicate_two" | awk '{print $1}')" = "$duplicate_two_before"
test ! -e "$duplicate_workspace/.wiki/raw/articles"

interrupted_workspace="$test_root/interrupted"
make_wiki "$interrupted_workspace"
interrupted_body='Interrupted body.'
interrupted_digest=$(printf '%s\n' "$interrupted_body" | shasum -a 256 | awk '{print $1}')
interrupted_uri="wiki://workspace/evidence/$interrupted_digest"
interrupted_legacy="$interrupted_workspace/.wiki/raw/interrupted.md"
legacy_record "$interrupted_legacy" 'Interrupted' 'https://example.test/interrupted' "$interrupted_uri" "$interrupted_body"
python3 - "$ambient" "$interrupted_workspace" <<'PY'
import importlib.util
import sys
from pathlib import Path

ambient, workspace = map(Path, sys.argv[1:])
sys.path.insert(0, str(ambient.parent))
spec = importlib.util.spec_from_file_location("wiki_ambient_interruption", ambient)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.migration_atomic_bytes

def fail_after_replace(path, payload):
    original(path, payload)
    raise RuntimeError("injected interruption")

module.migration_atomic_bytes = fail_after_replace
try:
    module.migrate_canonical_evidence(str(workspace))
except RuntimeError as error:
    assert str(error) == "injected interruption"
else:
    raise AssertionError("injected interruption was not observed")
assert (workspace / ".wiki/raw/interrupted.md").is_file()
targets = list((workspace / ".wiki/raw/articles").glob("*.md"))
assert len(targets) == 1
PY
interrupted_result=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/config" \
  "$launcher" "$ambient" migrate-evidence --cwd "$interrupted_workspace")
assert_count 1 "$interrupted_result"
test ! -e "$interrupted_legacy"
test "$(find "$interrupted_workspace/.wiki/raw/articles" -type f -name '*.md' | wc -l | tr -d ' ')" -eq 1

foreign_workspace="$test_root/foreign"
mkdir -p "$foreign_workspace/.wiki/raw" "$foreign_workspace/.wiki/wiki"
printf '%s\n' '# Not a complete Wiki' >"$foreign_workspace/.wiki/config.md"
expect_rejected "$foreign_workspace" foreign-or-incomplete-wiki
test ! -e "$foreign_workspace/.wiki/raw/articles"

lint_bin=${LLM_WIKI_BIN:-}
test -n "$lint_bin" && test -x "$lint_bin"
lint_workspace="$test_root/lint"
mkdir -p "$lint_workspace/.wiki/raw/articles" "$lint_workspace/.wiki/raw/data" \
  "$lint_workspace/.wiki/raw/notes" "$lint_workspace/.wiki/raw/papers" "$lint_workspace/.wiki/raw/repos" \
  "$lint_workspace/.wiki/wiki/concepts" "$lint_workspace/.wiki/wiki/topics" \
  "$lint_workspace/.wiki/wiki/references" "$lint_workspace/.wiki/wiki/theses" "$lint_workspace/.wiki/output" \
  "$lint_workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$lint_workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$lint_workspace/.wiki/_index.md"
printf '%s\n' \
  '# Raw Sources Index' '' \
  '## Articles' 'See [articles/_index.md](articles/_index.md)' '' \
  '## Papers' 'See [papers/_index.md](papers/_index.md)' '' \
  '## Repos' 'See [repos/_index.md](repos/_index.md)' '' \
  '## Notes' 'See [notes/_index.md](notes/_index.md)' '' \
  '## Data' 'See [data/_index.md](data/_index.md)' >"$lint_workspace/.wiki/raw/_index.md"
printf '%s\n' \
  '# Wiki Articles' '' \
  '## Concepts' 'See [concepts/_index.md](concepts/_index.md)' '' \
  '## Topics' 'See [topics/_index.md](topics/_index.md)' '' \
  '## References' 'See [references/_index.md](references/_index.md)' '' \
  '## Theses' 'See [theses/_index.md](theses/_index.md)' >"$lint_workspace/.wiki/wiki/_index.md"
printf '%s\n' '# Output Artifacts' >"$lint_workspace/.wiki/output/_index.md"
for category in data notes papers repos; do
  printf '%s\n' "# ${category} Index" '' '> Generated by local llm-wiki lint.' '' '## Contents' '' \
    '| File | Summary | Tags | Updated |' '|------|---------|------|---------|' >"$lint_workspace/.wiki/raw/$category/_index.md"
done
for category in topics references theses; do
  printf '%s\n' "# ${category} Index" '' '> Generated by local llm-wiki lint.' '' '## Contents' '' \
    '| File | Summary | Tags | Updated |' '|------|---------|------|---------|' >"$lint_workspace/.wiki/wiki/$category/_index.md"
done
legacy_name=$(basename "$success_target")
cp "$success_target" "$lint_workspace/.wiki/raw/articles/$legacy_name"
fresh_input="$test_root/fresh-input.md"
printf '%s\n' 'Fresh evidence body.' >"$fresh_input"
fresh_result=$(HOME="$test_root/home" XDG_CONFIG_HOME="$test_root/lint-config" \
  "$launcher" "$ambient" canonicalize --cwd "$lint_workspace" --source "$fresh_input" \
  --source-url 'https://example.test/fresh' --title 'Fresh source')
fresh_target=$(printf '%s' "$fresh_result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])')
fresh_name=$(basename "$fresh_target")
printf '%s\n' \
  '# Articles Index' '' '> Generated by local llm-wiki lint.' '' '## Contents' '' \
  '| File | Summary | Tags | Updated |' '|------|---------|------|---------|' \
  "| [$legacy_name]($legacy_name) | Legacy web evidence | wiki-preflight, evidence, web-extraction | 2026-09-20 |" \
  "| [$fresh_name]($fresh_name) | Fresh web evidence | wiki-preflight, evidence, web-extraction | 2026-09-20 |" \
  >"$lint_workspace/.wiki/raw/articles/_index.md"
printf '%s\n' \
  '# Concepts Index' '' '> Generated by local llm-wiki lint.' '' '## Contents' '' \
  '| File | Summary | Tags | Updated |' '|------|---------|------|---------|' \
  '| [lint-fixture.md](lint-fixture.md) | Lint fixture article | backend, evidence | 2026-09-20 |' \
  >"$lint_workspace/.wiki/wiki/concepts/_index.md"
printf '%s\n' \
  '---' \
  'title: "Lint Fixture"' \
  'category: concept' \
  'summary: "Lint fixture article"' \
  'tags: [backend, evidence]' \
  'sources:' \
  "  - raw/articles/$legacy_name" \
  "  - raw/articles/$fresh_name" \
  'created: 2026-09-20' \
  'updated: 2026-09-20' \
  'verified: 2026-09-20' \
  'confidence: high' \
  'volatility: warm' \
  '---' \
  '' \
  '# Lint Fixture' \
  '' \
  'A lint fixture grounded in two canonical evidence records.' \
  '' \
  '## Sources' \
  '' \
  "- [$legacy_name](../../raw/articles/$legacy_name)" \
  "- [$fresh_name](../../raw/articles/$fresh_name)" \
  >"$lint_workspace/.wiki/wiki/concepts/lint-fixture.md"
set +e
lint_report=$(cd "$lint_workspace" && "$lint_bin" lint --local --json 2>&1)
lint_status=$?
set -e
test "$lint_status" -eq 0
printf '%s' "$lint_report" | python3 -c '
import json, sys
report = json.load(sys.stdin)
counts = report.get("counts", {})
assert counts.get("critical", 0) == 0, report
assert counts.get("warning", 0) == 0, report
assert counts.get("suggestion", 0) == 0, report
assert not any(".unknown" in str(item) for item in report.get("issues", [])), report
'
test ! -d "$lint_workspace/.wiki/inbox/.unknown"
test -z "$(find "$lint_workspace/.wiki/raw" -maxdepth 1 -type f -name '*.md' ! -name '_index.md' -print)"
! rg -n --hidden --glob '!**/.git/**' --glob '!tests/canonical-evidence.sh' \
  'llm-wiki[^\n]*--fix|--fix[^\n]*llm-wiki' "$root/plugins/wiki-preflight" "$root/tests"

printf '%s\n' 'canonical evidence migration tests passed'
