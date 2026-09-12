#!/bin/sh
set -eu

plugin_root=${1:-plugins/wiki-preflight}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
first="$test_root/first"
second="$test_root/second"
workspace="$test_root/workspace"
config_home="$test_root/config"
cp -R "$plugin_root" "$first"
cp -R "$plugin_root" "$second"
mkdir "$workspace"

XDG_CONFIG_HOME="$config_home" python3 "$first/scripts/wiki_ambient.py" map --cwd "$workspace" --topic persistent-topic >/dev/null
config="$config_home/llm-wiki/wiki-agent-system.json"
test -f "$config"
test "$(stat -f '%Lp' "$config")" = 600
test "$(XDG_CONFIG_HOME="$config_home" python3 "$second/scripts/wiki_ambient.py" resolve --cwd "$workspace" | python3 -c 'import json,sys; print(json.load(sys.stdin)["topic"])')" = persistent-topic
! grep -Fq 'persistent-topic' "$second/defaults/ambient.json"

printf '%s\n' 'config persistence passed'
