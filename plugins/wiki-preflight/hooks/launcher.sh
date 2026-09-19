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
vendor_root="$(CDPATH= cd -- "$(dirname -- "$0")/../vendor" 2>/dev/null && pwd || true)"
if [ -d "$vendor_root" ]; then
  if [ -n "${PYTHONPATH:-}" ]; then
    export PYTHONPATH="$vendor_root:$PYTHONPATH"
  else
    export PYTHONPATH="$vendor_root"
  fi
fi
exec python3 "$@"
