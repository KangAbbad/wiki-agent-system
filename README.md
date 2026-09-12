# Wiki Agent System

Repository-scoped knowledge preflight for Codex.

## Install

```bash
codex plugin marketplace add KangAbbad/wiki-agent-system --ref main
codex plugin add wiki-preflight@team-wiki
```

Commit `.wiki/` with repository knowledge. Keep `.wiki/.sessions/` untracked.

## Repository setup

Copy `templates/AGENTS.md` into the target repository root. The plugin reads
the local wiki preflight; the repository policy controls capture and placement.

## Structured capture

Use the bundled finalizer after meaningful work. It writes an atomic,
redacted, pending-curation record; it does not make unsupported claims
canonical.

```bash
python3 "$PLUGIN_ROOT/scripts/wiki_ambient.py" capture \
  --cwd "$PWD" \
  --outcome "Implemented webhook verification" \
  --kind result \
  --artifact src/webhook.ts \
  --decision "Verify signed payload before parsing" \
  --verification "bun run typecheck" \
  --source "https://example.com/official-spec" \
  --confidence high \
  --open-question "Add replay protection?"
```

Outcome is required. Optional fields: decisions, workspace-relative artifacts,
verification, attributable sources, confidence, and open questions.
Credential-like values are replaced with `[REDACTED]`.

## Device configuration

On first use, the plugin writes its mutable configuration and workspace-topic
mapping to `~/.config/llm-wiki/wiki-agent-system.json` (or
`$XDG_CONFIG_HOME/llm-wiki/wiki-agent-system.json`). Plugin updates never
overwrite this file.

## Storage control

`retention.py <workspace>` reports `.wiki/` usage against the user-configured
quota. `--apply` moves only expired autosaves and session state to `.wiki/.trash/`;
it never deletes or moves canonical `raw/`, `wiki/`, or `output/`.
