#!/usr/bin/env python3
"""Report and quarantine expired operational Wiki data; never delete knowledge."""

import argparse
import json
import os
import time
import uuid
from pathlib import Path


config_path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "llm-wiki" / "wiki-agent-system.json"
defaults = {"autosave_days": 90, "state_days": 30, "max_bytes": 1073741824}
if config_path.exists():
    try:
        retention = json.loads(config_path.read_text())["retention"]
        defaults.update({key: retention[key] for key in defaults if key in retention})
    except (KeyError, OSError, json.JSONDecodeError, TypeError):
        raise SystemExit("invalid user retention configuration")


parser = argparse.ArgumentParser()
parser.add_argument("workspace")
parser.add_argument("--apply", action="store_true", help="quarantine expired operational files")
parser.add_argument("--autosave-days", type=int, default=defaults["autosave_days"])
parser.add_argument("--state-days", type=int, default=defaults["state_days"])
parser.add_argument("--max-bytes", type=int, default=defaults["max_bytes"])
args = parser.parse_args()

if min(args.autosave_days, args.state_days, args.max_bytes) <= 0:
    raise SystemExit("retention days and max bytes must be positive")

workspace = Path(args.workspace).resolve()
wiki = workspace / ".wiki"
if not all((wiki / item).exists() for item in ("config.md", "_index.md", "raw", "wiki")):
    raise SystemExit("retention requires a valid local LLM Wiki")


def files_older_than(root: Path, days: int) -> list[Path]:
    cutoff = time.time() - days * 86400
    return [path for path in root.rglob("*") if path.is_file() and path.stat().st_mtime < cutoff] if root.exists() else []


def directory_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


operational = {
    "autosave": (wiki / "inbox" / "autosave", args.autosave_days),
    "state": (wiki / ".sessions", args.state_days),
}
expired = {name: files_older_than(root, days) for name, (root, days) in operational.items()}
quarantined = []
if args.apply:
    trash = wiki / ".trash"
    for category, paths in expired.items():
        source_root = operational[category][0]
        for path in paths:
            relative = path.relative_to(source_root)
            destination = trash / category / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if destination.exists():
                destination = destination.with_name(f"{destination.stem}-{uuid.uuid4().hex[:8]}{destination.suffix}")
            os.replace(path, destination)
            quarantined.append(str(destination))

used = directory_bytes(wiki)
percent = round(used * 100 / args.max_bytes, 2)
if percent >= 95:
    level = "block"
elif percent >= 85:
    level = "plan"
elif percent >= 70:
    level = "warn"
else:
    level = "normal"
print(json.dumps({
    "workspace": str(workspace),
    "quota": {"used_bytes": used, "max_bytes": args.max_bytes, "percent": percent, "level": level},
    "expired_operational_files": {name: [str(path) for path in paths] for name, paths in expired.items()},
    "quarantined": quarantined,
    "action": "quarantined" if args.apply else "dry-run",
    "canonical_excluded": ["raw", "wiki", "output"],
}))
