# P1 acceptance gate

Run `sh tests/p1-acceptance.sh` from a clean checkout. It prints one result
per control and ends with exactly `P1 ACCEPTANCE: PASS` or `P1 ACCEPTANCE: FAIL`.

The gate requires all controls below:

- Stop never interrupts the user-facing response; after a workspace change, a
  missed agent capture falls back quietly to a redacted structured capture of
  the real final message;
- semantic capture redacts credential-like values and persists required outcome;
- canonicalization accepts attributable evidence with SHA-256 provenance and
  rejects sensitive source material;
- bootstrap writes the current `.wiki-agent-system.json` schema marker;
- behavior matrix covers non-Git bootstrap, knowledge transfer, retention
  quarantine, additive migration, foreign `.wiki` protection, and docs routing;
- source and installed-package smoke tests pass;
- real Codex coexistence with `wiki@llm-wiki` passes;
- a fresh `CODEX_HOME` installs from the public Git marketplace and passes.

Any absent, skipped, or failing control is a P1 failure.
