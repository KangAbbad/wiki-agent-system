# Wiki Agent System

Scoped Workspace and User Wiki knowledge preflight for Codex.

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

No per-repository `AGENTS.md` copy is required. The plugin initializes a
missing Workspace Wiki only for a workspace path, and a missing User Wiki at
the configured LLM Wiki hub. Projectless/home sessions never create `.wiki/` in
the home directory. `UserPromptSubmit` always performs bounded retrieval;
`SubagentStart` receives task-scoped results; resume/compact re-queries the
latest Wiki from a private task descriptor. A generic follow-up with no ledger
references can derive a bounded query from only the Outcome of that same
session's pending or canonical Stop capture, then re-read current Wiki content;
it never borrows another session's capture. Without searchable terms or that session capture,
status remains `unavailable/no-content-terms`. Startup injects a short capsule, not the full
policy or index. The plugin never writes `AGENTS.md` into a user repository.

Wiki documents are untrusted reference data, not agent instructions. Workspace
knowledge is searched before User Wiki; private User Wiki/Mnemosyne results are
never copied into repository captures or artifacts. Meaningful projectless work
is auto-captured to User Wiki; meaningful workspace work to the local Wiki.
Follow-ups, resume/compact, and subagents reuse scope-bound references to
canonical knowledge, task output, and pending captures, then read the current
file contents again. Audit state stores no prompt/query or document body; User
Wiki references are kept only in the private User Wiki ledger.
`llm-wiki` session capture remains enabled. If its rehydration settings are
absent, Wiki Preflight turns those duplicate context injections off while
preserving any explicitly configured settings. Because Codex runs matching
plugin hooks concurrently, a first-startup race can still admit one upstream
digest before that setting is written; explicit user-enabled rehydration is
left unchanged. Review/trust changed plugin hooks in Codex before relying on
the installed runtime.

For Git workspaces, the hook adds `.wiki/.sessions/` to the repository's
`.gitignore` without touching non-Git workspaces. When a related commit is
authorized, include the relevant durable `.wiki/` knowledge; never stage the
runtime session directory. User Wiki/configuration and Mnemosyne remain outside
the repository boundary. Mnemosyne has no automatic team sync or shared-memory
path. If `HOME`, `XDG_CONFIG_HOME`, or their resolved private targets point
inside a Git repository, private-scope writes fail closed before creating
files there. Lifecycle hooks invoke a launcher that disables Python bytecode
writes and redirects any interpreter cache outside the worktree before Python
starts.

## Structured capture

The Stop hook owns the default semantic capture after meaningful work. It
records bounded intent, then writes one atomic, redacted, pending-curation
record from the final assistant message; it does not make unsupported claims
canonical. Optional structured enrichment preserves applicable outcome,
decisions, artifacts, verifications, sources, confidence, and open questions in
the same task record. Bounded Markdown sections named Outcome, Decisions,
Artifacts, Verification, Sources, Confidence, and Open questions are extracted
when present; unstructured replies remain bounded outcome captures. No terminal
invocation is required for preservation.

Credential-like values are replaced with `[REDACTED]`.

## Optional personal memory

Personal-scope handoff uses the optional `mnemosyne` CLI when available. The
adapter stores only one bounded, redacted preference/fact and a Wiki pointer;
it abstains on outcome-only or transcript-shaped prose and never writes
personal data to the repository Wiki. Session scope is the default. Set
`MNEMOSYNE_DEFAULT_SCOPE=global` only when global storage is explicitly
intended. Set `MNEMOSYNE_CLI` to a fixture or alternate CLI path for tests.
Missing, invalid, or timed-out adapters return diagnostics and leave the
capture flow successful.

`retrieve --prompt ...` checks the applicable Wiki for every prompt and is bounded. It checks records with
`status: canonical` and a valid `canonical_uri` in the Workspace Wiki first,
User Wiki records second, and private Mnemosyne hints last. Mnemosyne results
are hints, never evidence or proof.

## Device configuration

On first use, the plugin writes its mutable configuration and workspace-topic
mapping to `~/.config/llm-wiki/wiki-agent-system.json` (or
`$XDG_CONFIG_HOME/llm-wiki/wiki-agent-system.json`). Plugin updates never
overwrite this file.

Both this file and the private runtime marker at
`.wiki/.sessions/wiki-agent-system/marker.json` use forward-only schema
migrations. A newer unknown schema is left untouched until a compatible plugin
is installed. Existing root-level `.wiki-agent-system.json` markers migrate
there atomically before the legacy file is removed, so `llm-wiki lint --fix`
cannot quarantine plugin state.

## Upgrade safety

On task start, the hook provisions the installed runtime into stable plugin
data. After a later update removes a versioned cache, existing tasks continue
using that stable runtime; a new task provisions the newer runtime.

The optional YouTube transcript adapter and its pinned pure-Python dependencies
are vendored under `plugins/wiki-preflight/vendor/`, including upstream license
texts, a shipped-file hash manifest, and separate source-wheel hashes. The
stable launcher loads them locally; hooks never self-install or download Python
dependencies.

Every release follows the [Plugin Release SOP](docs/PLUGIN_RELEASE_SOP.md):
version increment, full acceptance gate, publish, marketplace refresh, the
activation boundary required for the release shape, scoped cache cleanup, and
post-install validation. Runtime-only updates use the stable runtime at the next
hook event; hook or skill changes require a new task and hook trust, with a full
app restart reserved for stale registration/cache recovery.

## Storage control

`retention.py <workspace>` reports `.wiki/` usage against the user-configured
quota. `--apply` moves only expired autosaves and session state to `.wiki/.trash/`;
it never deletes or moves canonical `raw/`, `wiki/`, or `output/`.
