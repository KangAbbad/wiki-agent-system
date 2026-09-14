#!/bin/sh
set -eu

# Set before Python starts. macOS CommandLineTools Python may cache stdlib
# bytecode below HOME before the Python program reaches its first line.
cache_root="${TMPDIR:-/tmp}/wiki-preflight-python-cache"
case "$cache_root" in
  /*) ;;
  *) cache_root="/tmp/wiki-preflight-python-cache" ;;
esac
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="$cache_root"
exec python3 "$@"
