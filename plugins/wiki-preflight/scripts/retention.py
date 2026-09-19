#!/usr/bin/env python3
"""Report, quarantine, and optionally purge expired operational Wiki data."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - the package currently targets POSIX hosts.
    # ponytail: POSIX-only lock; add a platform adapter if Windows support is required.
    fcntl = None


MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60
LOCK_TIMEOUT_SECONDS = 0.5
STATE_SCHEMA_VERSION = 1
STATE_FILENAME = "retention.json"
LOCK_FILENAME = "retention.lock"
RECEIPT_DIRNAME = "youtube-receipts"
RECEIPT_ID = re.compile(r"^[0-9a-f]{32}$")
RECEIPT_REFERENCE = re.compile(r"^\s*receipt_id:\s*[\"']?([0-9a-f]{32})[\"']?\s*$", re.M)

config_path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "llm-wiki" / "wiki-agent-system.json"
defaults = {"autosave_days": 10, "queue_days": 30, "state_days": 30, "trash_days": 7, "max_bytes": 1073741824}
if config_path.exists():
    try:
        retention = json.loads(config_path.read_text())["retention"]
        if not isinstance(retention, dict):
            raise TypeError
        defaults.update({key: retention[key] for key in defaults if key in retention})
    except (KeyError, OSError, TypeError, json.JSONDecodeError):
        raise SystemExit("invalid user retention configuration")
if any(type(value) is not int or value <= 0 for value in defaults.values()):
    raise SystemExit("invalid user retention configuration")


def atomic_json_write(path: Path, data: dict):
    if path.parent.is_symlink():
        raise OSError("retention state directory is a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".retention-", text=True)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, sort_keys=True)
            file.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def valid_local_wiki(wiki: Path) -> bool:
    return (
        wiki.is_dir()
        and not wiki.is_symlink()
        and (wiki / "config.md").is_file()
        and (wiki / "_index.md").is_file()
        and (wiki / "raw").is_dir()
        and (wiki / "wiki").is_dir()
    )


def files_older_than(root: Path, days: int, excluded: set[Path] | None = None) -> list[Path]:
    excluded = excluded or set()
    cutoff = time.time() - days * 86400
    if not root.exists() or root.is_symlink():
        return []
    expired = []
    for path in root.rglob("*"):
        try:
            if path in excluded or path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_mtime < cutoff:
                expired.append(path)
        except OSError:
            continue
    return expired


def referenced_receipt_ids(wiki: Path) -> set[str]:
    referenced = set()
    for section in ("raw", "wiki", "output"):
        root = wiki / section
        if root.is_symlink() or not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            referenced.update(RECEIPT_REFERENCE.findall(content))
    return referenced


def referenced_receipt_paths(wiki: Path, receipt_ids: set[str]) -> set[Path]:
    active = wiki / ".sessions" / "wiki-agent-system" / RECEIPT_DIRNAME
    quarantine = wiki / ".trash" / "state" / "wiki-agent-system" / RECEIPT_DIRNAME
    protected = set()
    for receipt_id in receipt_ids:
        if not RECEIPT_ID.fullmatch(receipt_id):
            continue
        active_path = active / f"{receipt_id}.json"
        if active_path.is_file() and not active_path.is_symlink():
            protected.add(active_path)
        if quarantine.is_dir() and not quarantine.is_symlink():
            for path in quarantine.glob(f"{receipt_id}*.json"):
                if path.is_file() and not path.is_symlink() and (path.stem == receipt_id or path.stem.startswith(f"{receipt_id}-")):
                    protected.add(path)
    return protected


def storage_report(wiki: Path):
    active = quarantine = total = 0
    if not wiki.exists() or wiki.is_symlink():
        return {"active_autosave_bytes": active, "quarantine_bytes": quarantine, "total_bytes": total}
    for path in wiki.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            relative = path.relative_to(wiki)
            total += size
            if relative.parts[:2] == ("inbox", "autosave"):
                active += size
            elif relative.parts[:1] == (".trash",):
                quarantine += size
        except OSError:
            continue
    return {"active_autosave_bytes": active, "quarantine_bytes": quarantine, "total_bytes": total}


def storage_diagnostics(storage: dict):
    if not storage["quarantine_bytes"]:
        return []
    return [{
        "code": "quarantine-counts-toward-quota",
        "message": "Quarantine does not reclaim disk: quarantined files remain on disk and count toward the total Wiki quota.",
        "quarantine_bytes": storage["quarantine_bytes"],
    }]


def read_state(path: Path):
    if path.is_symlink():
        return "invalid", None
    if not path.exists():
        return "missing", None
    try:
        data = json.loads(path.read_text())
        last_run = data["last_run_at"]
        if data.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError
        if type(last_run) not in (int, float) or not math.isfinite(last_run) or last_run < 0:
            raise ValueError
    except (KeyError, OSError, OverflowError, TypeError, ValueError, json.JSONDecodeError):
        return "invalid", None
    return "valid", float(last_run)


def acquire_lock(path: Path):
    if fcntl is None:
        return None, "locking-unavailable"
    if path.parent.is_symlink():
        return None, "lock-directory-is-symlink"
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        os.fchmod(fd, 0o600)
    except OSError:
        return None, "lock-open-failed"

    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, None
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None, "lock-timeout"
            time.sleep(0.01)
        except OSError:
            os.close(fd)
            return None, "lock-failed"


def release_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def destination_for(trash: Path, category: str, relative: Path) -> Path:
    if trash.is_symlink() or (trash.exists() and not trash.is_dir()):
        raise OSError("retention trash is not a local directory")
    trash.mkdir(mode=0o700, parents=True, exist_ok=True)
    boundary = trash.resolve()
    destination = trash / category / relative
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.parent.is_symlink() or not destination.parent.resolve().is_relative_to(boundary):
        raise OSError("retention destination leaves local trash")
    return destination


def operational_files(wiki: Path, autosave_days: int, queue_days: int, state_days: int, state_path: Path, lock_path: Path):
    excluded = {state_path, lock_path, wiki / ".sessions" / "wiki-agent-system" / "capture.lock"}
    queue_root = wiki / ".sessions" / "wiki-agent-system" / "youtube-queues"
    receipt_root = wiki / ".sessions" / "wiki-agent-system" / RECEIPT_DIRNAME
    foreground_root = wiki / ".sessions" / "wiki-agent-system" / "foreground-loops"
    queue_excluded = {
        path for path in queue_root.rglob("*") if path.is_file() and path.name.endswith(".lock")
    } if queue_root.is_dir() and not queue_root.is_symlink() else set()
    receipt_ids = referenced_receipt_ids(wiki)
    receipt_excluded = {
        path for path in receipt_root.rglob("*") if path.is_file() and path.name.endswith(".lock")
    } if receipt_root.is_dir() and not receipt_root.is_symlink() else set()
    receipt_excluded.update(receipt_root / f"{receipt_id}.json" for receipt_id in receipt_ids)
    foreground_excluded = {
        path for path in foreground_root.rglob("*") if path.is_file() and path.name.endswith(".lock")
    } if foreground_root.is_dir() and not foreground_root.is_symlink() else set()
    queue_files = files_older_than(queue_root, queue_days, queue_excluded)
    state_files = [
        path for path in files_older_than(wiki / ".sessions", state_days, excluded)
        if not path.is_relative_to(queue_root) and not path.is_relative_to(receipt_root) and path not in foreground_excluded
    ]
    receipt_files = files_older_than(receipt_root, state_days, receipt_excluded)
    return {
        "autosave": files_older_than(wiki / "inbox" / "autosave", autosave_days, excluded),
        "state": state_files + queue_files + receipt_files,
    }


def quarantine(wiki: Path, expired: dict[str, list[Path]]):
    trash = wiki / ".trash"
    quarantined = []
    for category, paths in expired.items():
        source_root = wiki / ("inbox/autosave" if category == "autosave" else ".sessions")
        for path in paths:
            relative = path.relative_to(source_root)
            destination = destination_for(trash, category, relative)
            if os.path.lexists(destination):
                destination = destination.with_name(f"{destination.stem}-{uuid.uuid4().hex[:8]}{destination.suffix}")
            os.replace(path, destination)
            try:
                os.utime(destination, None)
            except OSError:
                os.replace(destination, path)
                raise
            quarantined.append(str(destination))
    return quarantined


def empty_purge_summary():
    return {
        "files": 0,
        "bytes": 0,
        "errors": 0,
        "by_category": {category: {"files": 0, "bytes": 0} for category in ("autosave", "state")},
    }


def purge_quarantine(wiki: Path, days: int, protected: set[Path]):
    summary = empty_purge_summary()
    trash = wiki / ".trash"
    if trash.is_symlink():
        summary["errors"] = 1
        return summary
    if not trash.exists():
        return summary
    if not trash.is_dir():
        summary["errors"] = 1
        return summary
    boundary = trash.resolve()
    for category in ("autosave", "state"):
        root = trash / category
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            summary["errors"] += 1
            continue
        if not root.exists():
            continue
        root_boundary = root.resolve()
        if not root_boundary.is_relative_to(boundary):
            summary["errors"] += 1
            continue
        for path in files_older_than(root, days):
            if path in protected:
                continue
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root_boundary):
                    summary["errors"] += 1
                    continue
                size = path.stat().st_size
                path.unlink()
            except OSError:
                summary["errors"] += 1
                continue
            summary["files"] += 1
            summary["bytes"] += size
            summary["by_category"][category]["files"] += 1
            summary["by_category"][category]["bytes"] += size
    return summary


def apply_retention(wiki: Path, autosave_days: int, queue_days: int, state_days: int, trash_days: int, scheduled: bool):
    state_path = wiki / ".sessions" / STATE_FILENAME
    lock_path = wiki / ".sessions" / LOCK_FILENAME
    if scheduled:
        state_status, last_run = read_state(state_path)
        if state_status == "invalid":
            return {}, [], empty_purge_summary(), "skipped", "invalid-state"
        if state_status == "valid" and time.time() - last_run < MAINTENANCE_INTERVAL_SECONDS:
            return {}, [], empty_purge_summary(), "skipped", "not-due"

    fd, reason = acquire_lock(lock_path)
    if fd is None:
        return {}, [], empty_purge_summary(), "skipped", reason
    try:
        if scheduled:
            state_status, last_run = read_state(state_path)
            if state_status == "invalid":
                return {}, [], empty_purge_summary(), "skipped", "invalid-state"
            if state_status == "valid" and time.time() - last_run < MAINTENANCE_INTERVAL_SECONDS:
                return {}, [], empty_purge_summary(), "skipped", "not-due"
        expired = operational_files(wiki, autosave_days, queue_days, state_days, state_path, lock_path)
        quarantined = quarantine(wiki, expired)
        referenced = referenced_receipt_paths(wiki, referenced_receipt_ids(wiki))
        protected = set(map(Path, quarantined)) | referenced
        purged = purge_quarantine(wiki, trash_days, protected) if scheduled else empty_purge_summary()
        if scheduled:
            atomic_json_write(state_path, {"last_run_at": time.time(), "schema_version": STATE_SCHEMA_VERSION})
        return expired, quarantined, purged, "quarantined", None
    finally:
        release_lock(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("--apply", action="store_true", help="quarantine expired operational files")
    parser.add_argument("--scheduled", action="store_true", help="apply the SessionStart cadence")
    parser.add_argument("--autosave-days", type=int, default=defaults["autosave_days"])
    parser.add_argument("--queue-days", type=int, default=defaults["queue_days"])
    parser.add_argument("--state-days", type=int, default=defaults["state_days"])
    parser.add_argument("--trash-days", type=int, default=defaults["trash_days"])
    parser.add_argument("--max-bytes", type=int, default=defaults["max_bytes"])
    args = parser.parse_args()

    if min(args.autosave_days, args.queue_days, args.state_days, args.trash_days, args.max_bytes) <= 0:
        raise SystemExit("retention days and max bytes must be positive")

    workspace = Path(args.workspace).resolve()
    wiki = workspace / ".wiki"
    if not valid_local_wiki(wiki):
        raise SystemExit("retention requires a valid local LLM Wiki")

    state_path = wiki / ".sessions" / STATE_FILENAME
    lock_path = wiki / ".sessions" / LOCK_FILENAME
    if args.apply:
        expired, quarantined, purged, action, skip_reason = apply_retention(
            wiki, args.autosave_days, args.queue_days, args.state_days, args.trash_days, args.scheduled
        )
    else:
        expired = operational_files(wiki, args.autosave_days, args.queue_days, args.state_days, state_path, lock_path)
        quarantined = []
        purged = empty_purge_summary()
        action = "dry-run"
        skip_reason = None

    storage = storage_report(wiki)
    used = storage["total_bytes"]
    percent = round(used * 100 / args.max_bytes, 2)
    if percent >= 95:
        level = "block"
    elif percent >= 85:
        level = "plan"
    elif percent >= 70:
        level = "warn"
    else:
        level = "normal"
    result = {
        "workspace": str(workspace),
        "storage": storage,
        "diagnostics": storage_diagnostics(storage),
        "quota": {"used_bytes": used, "max_bytes": args.max_bytes, "percent": percent, "level": level, "basis": "total_bytes"},
        "expired_operational_files": {name: [str(path) for path in paths] for name, paths in expired.items()},
        "quarantined": quarantined,
        "purge": {
            "enabled": bool(args.apply and args.scheduled),
            "older_than_days": args.trash_days,
            "scope": [".trash/autosave", ".trash/state"],
            "summary": purged,
        },
        "action": action,
        "canonical_excluded": ["raw", "wiki", "output"],
    }
    if skip_reason:
        result["skip_reason"] = skip_reason
    print(json.dumps(result))


if __name__ == "__main__":
    main()
