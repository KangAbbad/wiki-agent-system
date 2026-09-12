#!/usr/bin/env python3
import json, sys, os, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
cwd = Path(payload.get("cwd") or payload.get("workspace_root") or ".").resolve()
existing = next((p / ".wiki" for p in (cwd, *cwd.parents) if (p / ".wiki").is_dir()), None)
root = existing
if root and not all((root / item).exists() for item in ("config.md", "_index.md", "raw", "wiki")):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "SessionStart"), "additionalContext": f"Foreign/incomplete wiki at {root}; do not modify it."}}))
    raise SystemExit(0)
if root is None and cwd.is_dir():
    root = cwd / ".wiki"
    for path in (root / "raw", root / "wiki", root / "output", root / "inbox"):
        path.mkdir(parents=True, exist_ok=True)
    (root / "config.md").write_text("# Workspace Wiki\n")
    (root / "_index.md").write_text("# Workspace Wiki\n\n## Knowledge\n\n- [Raw](raw/)\n- [Articles](wiki/)\n- [Output](output/)\n")
if root:
    event = payload.get("hook_event_name", "SessionStart")
    if event == "Stop":
        captures = root / "inbox" / "autosave"
        captures.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = captures / f"{stamp}-{uuid.uuid4().hex[:8]}-session.md"
        fd, temporary = tempfile.mkstemp(dir=captures, prefix=".capture-")
        with os.fdopen(fd, "w") as file:
            file.write(
            f"---\ntype: autosave-capture\nstatus: pending-curation\nworkspace: {cwd}\n---\n\n# Session capture\n\nSession completed. Review changed workspace artifacts during curation.\n"
            )
        os.replace(temporary, output)
    policy = (Path(__file__).resolve().parents[1] / "defaults" / "policy.md").read_text().strip()
    index = (root / "_index.md").read_text()[:4000]
    captures = sorted((root / "inbox" / "autosave").glob("*.md"))[-3:]
    recent = "\n\n".join(path.read_text()[:2000] for path in captures)
    text = f"{policy}\nWorkspace knowledge index:\n{index}\nRecent captures:\n{recent}"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "SessionStart"), "additionalContext": text}}))
