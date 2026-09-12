#!/usr/bin/env python3
"""Atomically publish the installed plugin runtime to stable plugin data."""

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path


root = Path(os.environ["PLUGIN_ROOT"]).resolve()
data = Path(os.environ["PLUGIN_DATA"]).resolve()
version = json.loads((root / ".codex-plugin" / "plugin.json").read_text())["version"]
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
