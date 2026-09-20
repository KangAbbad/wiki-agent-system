#!/bin/sh
set -eu

plugin_root=plugins/wiki-preflight
if [ "$#" -ge 1 ]; then plugin_root=$1; fi
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

"$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/evidence_verification.py" self-test

"$plugin_root/hooks/launcher.sh" - "$plugin_root/scripts/evidence_verification.py" <<'PY'
import importlib.util
import json
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("evidence_verification", __import__("sys").argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

for unsafe in ("http://127.0.0.1/private", "http://localhost/private", "https://user" + ":" + "credential" + "@example.test/private"):
    try:
        module.canonical_public_url(unsafe)
    except module.VerificationError:
        pass
    else:
        raise AssertionError("private or credential-bearing URL accepted")
assert module.prompt_items("Research https://example.test/spec")
assert module.prompt_items("Research https://example.test/spec")[0][2] is None
assert module.prompt_items("Research authority_host=trusted.example https://example.test/spec")[0][2] == "trusted.example"
assert module.prompt_items("Research PostgreSQL supports online index builds")[0][1] is None

root = Path(tempfile.mkdtemp())
wiki = root / ".wiki"
for name in ("raw", "wiki"):
    (wiki / name).mkdir(parents=True)
(wiki / "config.md").write_text("# Wiki\n")
(wiki / "_index.md").write_text("# Wiki\n")

calls = []
def fake_fetch(url, timeout):
    calls.append(url)
    if url.startswith(module.DISCOVERY_ENDPOINT):
        return {"kind": "success", "status_code": 200, "body": '<a href="https://official.example/docs">result</a>', "final_url": url}
    return {
        "kind": "success",
        "status_code": 200,
        "body": "<title>Fixture page</title>\nPostgreSQL supports online index builds.",
        "final_url": url,
    }
module.fetch_public_source = fake_fetch
queue_id, _ = module.ensure_queue(
    wiki,
    "PostgreSQL supports online index builds",
    "https://trusted.example/spec",
    "trusted.example",
)
first = module.drain_queue(wiki, queue_id, 2)
assert first["item"]["status"] == "verified", first
assert first["item"]["evidence_status"] == "verified"
assert first["item"]["provenance_class"] == "web-extraction"
assert first["item"]["evidence_eligible"] is True
assert first["item"]["transcript_eligible"] is False
raw = list((wiki / "raw" / "articles").glob("fixture-page-*.md"))
assert len(raw) == 1
raw_text = raw[0].read_text()
fields = module._frontmatter(raw_text)
assert fields["type"] == "articles"
assert fields["source_url"] == "https://trusted.example/spec"
assert fields["retrieval_method"] == "public-http"
assert fields["provenance_class"] == "web-extraction"
assert fields["evidence_status"] == "verified"
assert fields["canonical_uri"]
calls = []
same_id, _ = module.ensure_queue(
    wiki,
    "PostgreSQL supports online index builds",
    "https://trusted.example/spec",
    "trusted.example",
)
assert same_id == queue_id
assert module.drain_queue(wiki, queue_id, 2)["item"]["status"] == "verified"
assert calls == []
assert len(list((wiki / "raw" / "articles").glob("fixture-page-*.md"))) == 1

module.fetch_public_source = lambda url, timeout: {
    "kind": "success",
    "status_code": 200,
    "body": "It is not true that PostgreSQL supports online index builds." if not url.startswith(module.DISCOVERY_ENDPOINT) else "<html>no results</html>",
    "final_url": url,
}
unsupported_id, _ = module.ensure_queue(
    wiki,
    "PostgreSQL supports online index builds",
    "https://trusted.example/unsupported",
    "trusted.example",
)
unsupported = module.drain_queue(wiki, unsupported_id, 2)
assert unsupported["item"]["status"] == "exhausted"
assert unsupported["item"]["evidence_status"] == "unverified"
assert unsupported["item"]["error_class"] == "claim-not-supported"
claim = "PostgreSQL supports online index builds"
for denial in (
    "PostgreSQL supports online index builds only in a hypothetical example.",
    "A deprecated draft says PostgreSQL supports online index builds.",
    "PostgreSQL supports online index builds, but only in this fictional scenario.",
):
    assert module.claim_supported(claim, denial)[0] is False, denial

module.fetch_public_source = fake_fetch
unverified_id, _ = module.ensure_queue(
    wiki,
    "PostgreSQL supports online index builds",
    "https://example.test/page",
)
unverified = module.drain_queue(wiki, unverified_id, 2)
assert unverified["item"]["status"] == "exhausted"
assert unverified["item"]["evidence_status"] == "unverified"
assert unverified["item"]["provenance_class"] == "web-extraction"
assert unverified["item"]["error_class"] == "authority-binding-required"
assert "knowledge_readiness=ready" in module.context_for_item(wiki, unverified["item"])
unverified_path = wiki / ".sessions" / "wiki-agent-system" / module.QUEUE_DIRNAME / f"{unverified_id}.json"
unverified_data = json.loads(unverified_path.read_text())
unverified_data["items"][0]["next_retry_at"] = 0
unverified_path.write_text(json.dumps(unverified_data) + "\n")
scheduled_retry = module.drain_due_queue(wiki, deadline=2, limit=1)
assert scheduled_retry["processed"] == 1, scheduled_retry
assert module.queue_snapshot(wiki, unverified_id)["item"]["retry_count"] == 1

discovery_id, _ = module.ensure_queue(
    wiki,
    "PostgreSQL supports online index builds",
    None,
    "official.example",
)
discovered = module.drain_queue(wiki, discovery_id, 2)
assert discovered["item"]["status"] == "verified", discovered
assert module.queue_snapshot(wiki, discovery_id)["item"]["evidence_source_url"] == "https://official.example/docs"
assert any(url.startswith(module.DISCOVERY_ENDPOINT) for url in calls)
assert any("official.example" in url for url in calls)

sensitive_id, _ = module.ensure_queue(wiki, "Verify public page", "https://example.test/secret", "example.test")
module.fetch_public_source = lambda url, timeout: {
    "kind": "success" if not url.startswith(module.DISCOVERY_ENDPOINT) else "success",
    "status_code": 200,
    "body": "token=do-not-store" if not url.startswith(module.DISCOVERY_ENDPOINT) else "<html>no results</html>",
    "final_url": url,
}
sensitive = module.drain_queue(wiki, sensitive_id, 2)
assert sensitive["item"]["status"] == "blocked"
assert not any("do-not-store" in path.read_text() for path in (wiki / "raw").glob("*.md"))

module.fetch_public_source = fake_fetch
due_id, _ = module.ensure_queue(
    wiki,
    "PostgreSQL supports online index builds",
    "https://scheduled.example/page",
    "scheduled.example",
)
scheduled = module.drain_due_queue(wiki, deadline=2, limit=1)
assert scheduled["processed"] == 1, scheduled
assert module.queue_snapshot(wiki, due_id)["item"]["status"] == "verified"

future_id, _ = module.ensure_queue(wiki, "Future queue", "https://example.test/future")
future_path = wiki / ".sessions" / "wiki-agent-system" / module.QUEUE_DIRNAME / f"{future_id}.json"
future_path.write_text(json.dumps({"schema_version": 99}) + "\n")
before = future_path.read_bytes()
try:
    module.ensure_queue(wiki, "Future queue", "https://example.test/future")
except module.FutureSchema:
    pass
else:
    raise AssertionError("future queue accepted")
assert future_path.read_bytes() == before

assert module.eligibility("caption", "verified") == (True, True)
assert module.eligibility("web-extraction", "unverified") == (True, False)
assert module.eligibility("metadata", "verified") == (True, False)
assert module.eligibility("machine-transcription", "verified") == (False, False)
assert module.eligibility("none", "unverified") == (False, False)
assert module.knowledge_readiness("caption", "unverified") == "unready"
assert module.knowledge_readiness("web-extraction", "unverified") == "unready"
assert module.knowledge_readiness("caption", "unverified", True) == "ready"
assert module.knowledge_readiness("caption", "unverified", False) == "unready"
assert module.knowledge_readiness("web-extraction", "unverified", True) == "ready"
assert module.knowledge_readiness("web-extraction", "exhausted", True) == "unready"
assert module.knowledge_readiness("none", "unverified", True) == "unready"
print("generic evidence lifecycle passed")
PY

workspace="$test_root/workspace"
mkdir -p "$workspace/.wiki/raw" "$workspace/.wiki/wiki" "$workspace/.wiki/inbox"
printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/config.md"
printf '%s\n' '# Workspace Wiki' >"$workspace/.wiki/_index.md"
printf '%s\n' '# Extracted public page' >"$test_root/source.md"
result=$("$plugin_root/hooks/launcher.sh" "$plugin_root/scripts/wiki_ambient.py" canonicalize \
  --cwd "$workspace" --source "$test_root/source.md" \
  --source-url 'https://example.test/extracted' --title 'Extracted public page' \
  --provenance-class web-extraction --retrieval-method public-http)
printf '%s' "$result" | grep -q 'canonical-evidence'
raw=$(find "$workspace/.wiki/raw/articles" -type f -name 'extracted-public-page-*.md')
grep -q '^retrieval_method: public-http$' "$raw"
grep -q '^provenance_class: web-extraction$' "$raw"
grep -q '^evidence_status: unverified$' "$raw"
grep -q '^knowledge_readiness: ready$' "$raw"
grep -q '^evidence_eligible: true$' "$raw"
grep -q '^transcript_eligible: false$' "$raw"

printf '%s\n' 'evidence verification test passed'
