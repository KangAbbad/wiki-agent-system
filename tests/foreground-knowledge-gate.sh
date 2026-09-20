#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

"$plugin_root/hooks/launcher.sh" - "$plugin_root/hooks/preflight.py" "$test_root/workspace" <<'PY'
import contextlib
import hashlib
import importlib.util
import inspect
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

hook, workspace = map(Path, sys.argv[1:])
workspace.mkdir(parents=True)
wiki = workspace / ".wiki"
(wiki / "raw").mkdir(parents=True)
(wiki / "wiki").mkdir()
(wiki / "config.md").write_text("# Workspace Wiki\n")
(wiki / "_index.md").write_text("# Workspace Wiki\n")
spec = importlib.util.spec_from_file_location("preflight", hook)
module = importlib.util.module_from_spec(spec)
sys.stdin = io.StringIO(json.dumps({"cwd": str(workspace), "hook_event_name": "SessionStart"}))
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(module)
import youtube_fallback

assert "force_retry" not in inspect.signature(youtube_fallback.claim_queue_items).parameters
assert "force_retry" not in inspect.signature(youtube_fallback.drain_queue).parameters

def receipt_queue(queue_id, url):
    youtube_fallback.ensure_queue(wiki, [url], queue_id)
    video_id, canonical = youtube_fallback.canonical_video(url)
    caption = wiki / "inbox" / "youtube" / f"{video_id}.en.vtt"
    caption.parent.mkdir(parents=True, exist_ok=True)
    caption.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nGrounded fixture caption.\n")
    receipt = youtube_fallback.write_caption_receipt(wiki, canonical, video_id, 1, [caption])
    queue_path, _ = youtube_fallback.queue_paths(wiki, queue_id)
    status, data, error = youtube_fallback.read_queue(queue_path, queue_id)
    assert status == "valid", (status, error)
    item = data["items"][0]
    stamp = datetime.now(timezone.utc).isoformat()
    item.update(
        {
            "status": "ok",
            "attempt": 1,
            "terminal_at": stamp,
            "updated_at": stamp,
            "helper_status": "ok",
            "files": [Path(entry["path"]).name for entry in receipt["files"]],
            "receipt": receipt,
            "caption_state": "verified",
            "next_retry_at": None,
        }
    )
    youtube_fallback.queue_item_contract_defaults(item, wiki)
    data["updated_at"] = stamp
    youtube_fallback.atomic_queue_write(queue_path, data)
    return receipt, queue_path

def controller(queue_id, state, action, deadline=None):
    module.foreground_controller_directory(wiki)
    path, _ = module.foreground_controller_paths(wiki, queue_id)
    data = module.new_foreground_controller(queue_id)
    data.update({"state": state, "action": action})
    if deadline is not None:
        data["deadline_at"] = deadline
    module.atomic_queue_write(path, data)
    return path

url = youtube_fallback.canonical_video("https://youtu.be/dQw4w9WgXcQ")[1]
queue_id = module.queue_id_for("knowledge-session", "knowledge-turn")
receipt, queue_path = receipt_queue(queue_id, url)
controller_path = controller(queue_id, "evidence-ready", "knowledge-synthesis")
payload = {
    "session_id": "knowledge-session",
    "turn_id": "knowledge-turn",
    "last_assistant_message": "still synthesizing",
}
gate = module.stop_foreground_gate(wiki, payload)
assert gate["block"] is True and gate["capture"] is False, gate

body = f"""## Synthesis

The fixture synthesis is grounded in the caption source {url} and keeps the
claim tied to receipt {receipt['receipt_id']} and its verified hash.

## Sources

- {url} receipt={receipt['receipt_id']} hash={receipt['files'][0]['sha256']}

## Quality

Grounded claim mapping and provenance validation passed.
"""
artifact = wiki / "wiki" / "knowledge.md"
artifact_path = "wiki/knowledge.md"
artifact_hash = hashlib.sha256(body.encode()).hexdigest()
artifact.write_text(
    "\n".join(
        [
            "---",
            "type: youtube-knowledge",
            "schema: 1",
            "status: pending-curation",
            f"queue_id: {queue_id}",
            f"artifact_path: {artifact_path}",
            f"artifact_sha256: {artifact_hash}",
            f"receipt_ids: {receipt['receipt_id']}",
            f"receipt_hashes: {receipt['receipt_id']}/{receipt['files'][0]['sha256']}",
            f"source_urls: {url}",
            f"claim_evidence: {receipt['receipt_id']}/{receipt['files'][0]['sha256']}",
            "provenance_class: caption",
            "evidence_status: verified",
            "grounded: true",
            "quality_status: verified",
            "---",
            body,
        ]
    )
)
gate = module.stop_foreground_gate(wiki, payload)
assert gate["block"] is False and gate["capture"] is True, gate
assert module.read_foreground_controller(wiki, queue_id)[1]["state"] == "verified"

duplicate = wiki / "wiki" / "duplicate.md"
duplicate.write_text(artifact.read_text().replace("artifact_path: wiki/knowledge.md", "artifact_path: wiki/duplicate.md"))
gate = module.stop_foreground_gate(wiki, payload)
assert gate["block"] is True and gate["capture"] is False, gate
assert module.read_foreground_controller(wiki, queue_id)[1]["state"] == "evidence-ready"
duplicate.unlink()

artifact.write_text(artifact.read_text().replace("Grounded claim mapping", "Tampered claim mapping"))
gate = module.stop_foreground_gate(wiki, payload)
assert gate["block"] is True and gate["capture"] is False, gate
assert gate["reason"] == "Caption evidence is ready; finish the receipt-bound knowledge artifact before completing."

retry_id = module.queue_id_for("backoff-session", "backoff-turn")
retry_url = "https://youtu.be/9bZkp7q19f0"
youtube_fallback.ensure_queue(wiki, [retry_url], retry_id)
retry_path, _ = youtube_fallback.queue_paths(wiki, retry_id)
status, retry_data, error = youtube_fallback.read_queue(retry_path, retry_id)
assert status == "valid", (status, error)
retry_item = retry_data["items"][0]
retry_item.update(
    {
        "status": "retryable",
        "attempt": 1,
        "retry_count": 1,
        "helper_status": "error",
        "error_class": "network",
        "caption_state": "retryable",
        "next_retry_at": time.time() + 60,
    }
)
retry_data["updated_at"] = datetime.now(timezone.utc).isoformat()
youtube_fallback.atomic_queue_write(retry_path, retry_data)
retry_controller = controller(retry_id, "retryable", "caption-retry", time.time() + 2)
original_worker = youtube_fallback.run_queue_item
def forbidden_worker(*args, **kwargs):
    raise AssertionError("foreground continuation bypassed retry backoff")
youtube_fallback.run_queue_item = forbidden_worker
try:
    module.advance_foreground_controller(
        wiki,
        workspace,
        "",
        {"session_id": "backoff-session", "turn_id": "backoff-turn"},
    )
finally:
    youtube_fallback.run_queue_item = original_worker
status, retry_data, error = youtube_fallback.read_queue(retry_path, retry_id)
assert status == "valid", (status, error)
assert retry_data["items"][0]["status"] == "retryable", retry_data
assert retry_data["items"][0]["next_retry_at"] > time.time(), retry_data
retry_state = module.read_foreground_controller(wiki, retry_id)[1]
assert retry_state["state"] == "exhausted", retry_state
assert retry_state["revision"] == 1, retry_state
for _ in range(2):
    retry_gate = module.stop_foreground_gate(wiki, {
        "session_id": "backoff-session",
        "turn_id": "backoff-turn",
        "last_assistant_message": "bounded retry result",
    })
    assert retry_gate["block"] is False and retry_gate["capture"] is True, retry_gate

no_progress_id = module.queue_id_for("no-progress-session", "no-progress-turn")
no_progress_url = "https://youtu.be/aqz-KE-bpKQ"
youtube_fallback.ensure_queue(wiki, [no_progress_url], no_progress_id)
no_progress_controller = controller(no_progress_id, "pending", "caption-attempt", time.time() + 20)
original_drain = module.bounded_youtube_drain
module.bounded_youtube_drain = lambda *args, **kwargs: {"processed": 1}
try:
    module.advance_foreground_controller(
        wiki,
        workspace,
        "",
        {"session_id": "no-progress-session", "turn_id": "no-progress-turn"},
    )
finally:
    module.bounded_youtube_drain = original_drain
no_progress_state = module.read_foreground_controller(wiki, no_progress_id)[1]
assert no_progress_state["state"] == "exhausted", no_progress_state
assert no_progress_state["revision"] == 1, no_progress_state

print("foreground knowledge gate and backoff contract passed")
PY
