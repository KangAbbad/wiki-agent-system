#!/usr/bin/env python3
import json
import hashlib
import os
import sys
from pathlib import Path


event = json.load(sys.stdin)
if event.get("hook_event_name") != "Stop":
    raise SystemExit(0)

state_path = Path(os.environ["CODEX_FIXTURE_STATE"])
try:
    state = json.loads(state_path.read_text())
except (FileNotFoundError, json.JSONDecodeError):
    state = {"stop_count": 0, "events": []}
count = state.get("stop_count", 0) + 1
session_id = event.get("session_id")
turn_id = event.get("turn_id")
state["stop_count"] = count
state.setdefault("events", []).append(
    {
        "hook_event_name": event.get("hook_event_name"),
        "stop_hook_active": event.get("stop_hook_active"),
        "session_id_present": isinstance(session_id, str) and bool(session_id),
        "turn_id_present": isinstance(turn_id, str) and bool(turn_id),
        "session_id_hash": hashlib.sha256(session_id.encode()).hexdigest()[:12] if isinstance(session_id, str) else None,
        "turn_id_hash": hashlib.sha256(turn_id.encode()).hexdigest()[:12] if isinstance(turn_id, str) else None,
    }
)
state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, sort_keys=True) + "\n")

if count == 1:
    print(json.dumps({"decision": "block", "reason": "fixture continuation step=1"}))
else:
    print("{}")
