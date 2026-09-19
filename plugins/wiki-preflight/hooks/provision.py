#!/usr/bin/env python3
"""Atomically publish the installed plugin runtime to stable plugin data."""

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path


def verify_vendor_bundle(root: Path) -> None:
    vendor = root / "vendor"
    manifest = vendor / "MANIFEST.sha256"
    if manifest.is_symlink() or not manifest.is_file():
        raise RuntimeError("vendored runtime integrity failed: missing vendor/MANIFEST.sha256")

    expected: dict[str, str] = {}
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RuntimeError("vendored runtime integrity failed: unreadable vendor/MANIFEST.sha256") from error
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("  ", 1)
        if len(fields) != 2 or len(fields[0]) != 64 or any(char not in "0123456789abcdef" for char in fields[0]):
            raise RuntimeError(f"vendored runtime integrity failed: invalid manifest line {line_number}")
        relative = Path(fields[1])
        if relative.is_absolute() or relative.parts[:1] != ("vendor",) or ".." in relative.parts:
            raise RuntimeError(f"vendored runtime integrity failed: unsafe manifest path {fields[1]}")
        relative_name = relative.as_posix()
        if relative_name == "vendor/MANIFEST.sha256" or relative_name in expected:
            raise RuntimeError(f"vendored runtime integrity failed: duplicate manifest path {relative_name}")
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"vendored runtime integrity failed: missing {relative_name}")
        expected[relative_name] = fields[0]

    actual = {}
    for candidate in vendor.rglob("*"):
        relative_name = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            raise RuntimeError(f"vendored runtime integrity failed: symlink {relative_name}")
        if candidate.is_file() and relative_name != "vendor/MANIFEST.sha256":
            actual[relative_name] = candidate
    if not expected:
        raise RuntimeError("vendored runtime integrity failed: empty manifest")
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unlisted " + ", ".join(extra))
        raise RuntimeError("vendored runtime integrity failed: " + "; ".join(details))
    for relative_name, candidate in actual.items():
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected[relative_name]:
            raise RuntimeError(f"vendored runtime integrity failed: hash mismatch {relative_name}")


root = Path(os.environ["PLUGIN_ROOT"]).resolve()
data = Path(os.environ["PLUGIN_DATA"]).resolve()
version = json.loads((root / ".codex-plugin" / "plugin.json").read_text())["version"]
vendor = root / "vendor"
required_vendor_files = (
    vendor / "NOTICE.md",
    vendor / "MANIFEST.sha256",
    vendor / "SOURCES.sha256",
    vendor / "youtube_transcript_api-1.2.4.dist-info" / "METADATA",
)
missing_vendor_files = [str(path.relative_to(root)) for path in required_vendor_files if not path.is_file()]
if missing_vendor_files:
    raise RuntimeError("incomplete vendored runtime: " + ", ".join(missing_vendor_files))
verify_vendor_bundle(root)
runtimes = data / "runtimes"
runtimes.mkdir(mode=0o700, parents=True, exist_ok=True)
runtime = runtimes / version
if not runtime.exists():
    staging = Path(tempfile.mkdtemp(dir=runtimes, prefix=".runtime-"))
    shutil.rmtree(staging)
    try:
        shutil.copytree(root, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        os.replace(staging, runtime)
    except FileExistsError:
        shutil.rmtree(staging, ignore_errors=True)
pointer = data / "current"
temporary = data / f".current-{uuid.uuid4().hex}"
os.symlink(f"runtimes/{version}", temporary)
os.replace(temporary, pointer)
