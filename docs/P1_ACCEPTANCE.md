# P1 acceptance gate

Run `sh tests/p1-acceptance.sh` from a clean checkout. It prints one result
per control and ends with exactly `P1 ACCEPTANCE: PASS` or `P1 ACCEPTANCE: FAIL`.

The gate requires all controls below:

- Stop never interrupts the user-facing response; a durable prompt or workspace
  change creates a redacted pending capture of the real final message without
  requiring an agent capture command;
- structured final-response sections are bounded and preserve decisions,
  artifacts, verification, sources, confidence, and open questions when present;
- semantic capture redacts credential-like values and persists required outcome;
- canonicalization accepts attributable evidence with SHA-256 provenance and
  rejects sensitive source material;
- canonical evidence writers emit `raw/articles` records, and the explicit
  migration path preserves eligible legacy evidence without touching ordinary
  raw records or private runtime state;
- `tests/canonical-evidence.sh` runs a read-only LLM Wiki lint fixture when
  `LLM_WIKI_BIN` points to the tested `llm-wiki` binary; it requires zero
  critical, warning, or suggestion findings and never invokes `--fix`;
- public web extraction preserves URL, retrieval method/time, hash, provenance,
  and an explicit evidence lifecycle without upgrading it to caption evidence;
- bounded public verification deduplicates claims, retains unverified/exhausted
  outcomes, and requests users only for precise authority boundaries;
- bootstrap writes the current private runtime schema marker under
  `.wiki/.sessions/wiki-agent-system/`, and atomically migrates a supported
  legacy root marker without exposing it to `llm-wiki` lint;
- behavior matrix covers non-Git bootstrap, knowledge transfer, retention
  quarantine, additive migration, foreign `.wiki` protection, and docs routing;
- source and installed-package smoke tests pass;
- real Codex coexistence with `wiki@llm-wiki` passes;
- a fresh `CODEX_HOME` installs from the public Git marketplace and passes.
- Python compilation, shell syntax, JSON validation, diff hygiene, and a
  credential-pattern scan pass before release.

Any absent, skipped, or failing control is a P1 failure.
