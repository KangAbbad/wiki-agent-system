#!/usr/bin/env python3
import argparse
import json
import os
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("workspace")
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
root = Path(args.workspace) / ".wiki" / "inbox" / "autosave"
cutoff = time.time() - 90 * 86400
expired = [path for path in root.glob("*.md") if path.stat().st_mtime < cutoff] if root.exists() else []
trash = Path(args.workspace) / ".wiki" / ".trash" / "autosave"
if args.apply:
    trash.mkdir(parents=True, exist_ok=True)
    for path in expired:
        os.replace(path, trash / path.name)
print(json.dumps({"expired_autosaves": [str(path) for path in expired], "action": "quarantined" if args.apply else "dry-run"}))
