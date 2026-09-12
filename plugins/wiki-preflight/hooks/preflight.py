#!/usr/bin/env python3
import json, sys
from pathlib import Path

payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
cwd = Path(payload.get("cwd") or payload.get("workspace_root") or ".").resolve()
root = next((p / ".wiki" for p in (cwd, *cwd.parents) if (p / ".wiki" / "_index.md").is_file()), None)
if root is None and cwd.is_dir():
    root = cwd / ".wiki"
    for path in (root / "raw", root / "wiki", root / "output", root / "inbox"):
        path.mkdir(parents=True, exist_ok=True)
    (root / "config.md").write_text("# Workspace Wiki\n")
    (root / "_index.md").write_text("# Workspace Wiki\n\n## Knowledge\n\n- [Raw](raw/)\n- [Articles](wiki/)\n- [Output](output/)\n")
if root:
    text = f"Workspace knowledge preflight: read {root / '_index.md'} and relevant recent captures/articles before working."
    print(json.dumps({"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "SessionStart"), "additionalContext": text}}))
