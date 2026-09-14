# Wiki Agent System

Repository-scoped knowledge preflight for Codex.

The package also ships `wiki-preflight:wiki-ambient`: the global policy for
projectless, user-scope knowledge work. Team devices do not need a separate
personal `~/.codex/skills/wiki-ambient` installation.

## Install

```bash
codex plugin marketplace add KangAbbad/wiki-agent-system --ref main
codex plugin add wiki-preflight@wiki-agent-system
```

Commit durable repository `.wiki/` knowledge only as part of a user-authorized
commit. Keep `.wiki/.sessions/` untracked; the plugin never stages or commits.

## Repository setup

No per-repository `AGENTS.md` copy is required. On every SessionStart and
UserPromptSubmit, the plugin initializes or validates the workspace wiki and
injects its repository policy into the agent context. This applies to empty and
non-Git workspaces as well. The plugin never writes `AGENTS.md` into a user
repository.

For Git workspaces, the hook adds `.wiki/.sessions/` to the repository's
`.gitignore` without touching non-Git workspaces. When a related commit is
authorized, include the relevant durable `.wiki/` knowledge; never stage the
runtime session directory. User Wiki/configuration and Mnemosyne remain outside
the repository boundary. Mnemosyne has no automatic team sync or shared-memory
path. If `HOME`, `XDG_CONFIG_HOME`, or their resolved private targets point
inside a Git repository, the capture command fails closed before creating
private files there. Lifecycle hooks invoke a launcher that disables Python
bytecode writes and redirects any interpreter cache outside the worktree before
Python starts.

## Structured capture

Use the bundled finalizer after meaningful work. It writes an atomic,
redacted, pending-curation record; it does not make unsupported claims
canonical.

```bash
"$PLUGIN_ROOT/hooks/launcher.sh" "$PLUGIN_ROOT/scripts/wiki_ambient.py" capture \
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

## Optional personal memory

`capture --scope personal` uses the optional `mnemosyne` CLI when available.
The adapter stores only one bounded, redacted preference/fact supplied through
an explicit `--decision` field and a Wiki pointer; it abstains on outcome-only
or transcript-shaped prose and never writes personal data to the repository
Wiki. Session scope is the default. Set `MNEMOSYNE_DEFAULT_SCOPE=global` only
when global storage is explicitly intended. Set `MNEMOSYNE_CLI` to a fixture
or alternate CLI path for tests. Missing, invalid, or timed-out adapters
return diagnostics and leave the capture flow successful.

`retrieve --prompt ...` is intent-gated and bounded. It checks records with
`status: canonical` and a valid `canonical_uri` in the Workspace Wiki first,
User Wiki records second, and private Mnemosyne hints last. Mnemosyne results
are hints, never evidence or proof.

## Device configuration

On first use, the plugin writes its mutable configuration and workspace-topic
mapping to `~/.config/llm-wiki/wiki-agent-system.json` (or
`$XDG_CONFIG_HOME/llm-wiki/wiki-agent-system.json`). Plugin updates never
overwrite this file.

Both this file and `.wiki/.wiki-agent-system.json` use forward-only schema
migrations. A newer unknown schema is left untouched until a compatible plugin
is installed.

## Upgrade safety

On task start, the hook provisions the installed runtime into stable plugin
data. After a later update removes a versioned cache, existing tasks continue
using that stable runtime; a new task provisions the newer runtime.

## Storage control

`retention.py <workspace>` reports `.wiki/` usage against the user-configured
quota. `--apply` moves only expired autosaves and session state to `.wiki/.trash/`;
it never deletes or moves canonical `raw/`, `wiki/`, or `output/`.
