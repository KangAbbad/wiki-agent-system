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
