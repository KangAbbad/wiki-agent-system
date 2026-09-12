#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("workspace")
args = parser.parse_args()
root = Path(args.workspace) / ".wiki" / "inbox" / "autosave"
cutoff = time.time() - 90 * 86400
expired = [str(path) for path in root.glob("*.md") if path.stat().st_mtime < cutoff] if root.exists() else []
print(json.dumps({"expired_autosaves": expired, "action": "dry-run"}))
