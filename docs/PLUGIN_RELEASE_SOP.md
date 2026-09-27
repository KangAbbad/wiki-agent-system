# Wiki Preflight Release SOP

Use this procedure for every shipped improvement or bug fix. A source-tree
change is not a release.

## 1. Classify and version

Update `plugins/wiki-preflight/.codex-plugin/plugin.json` before the release
commit.

| Change | Version increment |
|---|---|
| Compatible bug fix | patch (`0.7.0` → `0.7.1`) |
| New behavior, schema migration, or user-visible capability | minor (`0.7.0` → `0.8.0`) |
| Contract that cannot migrate safely | major |

Internal schemas may migrate independently, but an unknown future schema must
remain read-only. Do not reuse a previously published plugin version.

## 2. Release gate

From a clean source checkout, run the full gate:

```sh
sh tests/p1-acceptance.sh
```

It must end with `P1 ACCEPTANCE: PASS`. This includes source, installed
runtime, stable-runtime, coexistence, and fresh Git marketplace validation.
Also require Python compilation, shell/JSON validation, `git diff --check`, and
credential scan to pass. Any skipped, warning, or failed required gate blocks
release.

## 3. Publish candidate

Review the staged diff for secrets and environment-specific identifiers. Commit
the version bump and verified change, then push the exact commit to `main`.
Record the commit SHA and version in the release report.

## 4. Refresh installation and activate

After the push, refresh the marketplace snapshot and install the released
package:

```sh
codex plugin marketplace upgrade wiki-agent-system
codex plugin add wiki-preflight@wiki-agent-system
```

Then classify activation before validating:

| Release shape | Activation requirement |
|---|---|
| Runtime implementation only; no manifest, `hooks/hooks.json`, skill, or plugin enablement change | Let the next trusted lifecycle hook provision the installed package, then verify `$PLUGIN_DATA/current` points to the released runtime. The next lifecycle hook uses that runtime without quitting Codex. |
| Any skill, manifest, hook definition, trust-hash, MCP, or plugin enablement change | Start a **new Codex task** after installation and trust the changed hooks when prompted. Do not expect an already-open task to receive newly loaded skill instructions or hook registrations. |
| Desktop catalog/cache does not recognize the installed version, or post-install validation fails because of stale registration | Fully quit and reopen Codex, then retry validation. This is recovery, not the normal release path. |

Codex documents a new session as the activation boundary for bundled skills and
tools. A running task's model context cannot be hot-reloaded. The stable runtime
is intentionally separate: installed hook commands dispatch to
`$PLUGIN_DATA/current`, so implementation-only updates can take effect at the
next hook event after atomic provisioning.

## 5. Mandatory cache cleanup

For routine version-pruning after a successful release, clean caches only
after steps 2–4 succeed, the required activation boundary is met, and no
active task is using the old runtime. Failed-validation recovery follows the
separate procedure below.

1. Verify the new installed cache version and the stable runtime target match.
2. Retain the current stable runtime and one immediately previous runtime for
   rollback.
3. Remove only superseded **versioned marketplace cache directories** beneath:

   ```text
   ~/.codex/plugins/cache/wiki-agent-system/wiki-preflight/<old-version>
   ```

   Never remove the newly installed version.
4. Remove stale Python bytecode cache entries only from:

   ```text
   ${TMPDIR:-/tmp}/wiki-preflight-python-cache
   ```

Do **not** remove any of the following: `~/.config/llm-wiki/`,
`.wiki/`, `.wiki/.sessions/`, receipts, queues, or
`~/.codex/plugins/data/wiki-preflight-wiki-agent-system/current`. These are
user/runtime state, not disposable package cache.

If a cache is suspected corrupt rather than merely superseded, use a clean
reinstall after quitting Codex: remove the plugin through Codex plugin
management, then install `wiki-preflight@wiki-agent-system` again. Reopen
Codex and complete the post-install validation below before resuming work.

### Failed-validation recovery: cleanup, then retry the identical prompt

For Wiki Preflight validation, treat the relevant plugin cache as the required
first recovery target whenever a check fails. This is an operational
troubleshooting rule, not proof that cache caused every failure.

1. Keep the same Codex task open and preserve the exact validation prompt. Do
   not rewrite it, switch sessions, or create a replacement task.
2. Clean only the affected stale/superseded plugin cache and the dedicated
   Python bytecode cache listed above. Preserve the active installed package,
   `$PLUGIN_DATA/current`, and the current plus immediately previous stable
   runtimes. If the active package itself appears corrupt, refresh/reinstall
   that exact plugin through Codex plugin management; never delete its active
   directory by hand.
3. Retry validation in the same task using the byte-for-byte identical prompt.
   This retry is mandatory after the targeted cleanup.
4. If the retry fails, stop and report both attempts, their errors, and the
   cache cleanup performed. Do not loop indefinitely or claim cache was the
   proven root cause without evidence. If Codex requires a restart before the
   active package can be refreshed, report the same-session retry as blocked;
   do not silently switch to a new session and call it a pass.

## 6. Post-install validation

For a runtime-only release, validate using the installed package after the
stable-runtime pointer check. For a release that changes skills, hook
registration, trust, MCP, or enablement, validate in a new Codex task. Verify:

- plugin information shows the released version;
- one `SessionStart`, `UserPromptSubmit`, and `Stop` hook run successfully;
- the stable runtime executes after a versioned cache removal test;
- no duplicate `wiki-preflight` hook registration exists;
- workspace/user configuration, Wiki captures, receipts, and queues remain
  intact.

For production release, also run fresh Git marketplace clean-device validation
against the pushed commit. Mark the release complete only when it passes.

## Report contract

Every progress, review, ratification, issue, failure, or success report must be
short, direct, and organized as a list. Lead with one explicit status:
`PASS`, `FAIL`, `PARTIAL`, `BLOCKED`, or `IN PROGRESS`. Include the applicable
items below; write `None` when there are no issues or no next action:

- **Result:** the central outcome in one sentence.
- **Completed:** concrete work that finished.
- **Issues / blockers:** every observed failure or unresolved issue, with its
  evidence and impact. Do not hide later observed failures behind the first.
- **Verification:** each relevant check as `PASS`, `FAIL`, `NOT RUN`, or
  `INTERRUPTED`; never imply an incomplete gate passed.
- **Artifacts:** changed files or reports, with direct paths or links.
- **State / next action:** commit, push, version, install, activation, and cache
  status when relevant; name one clear blocker or next action.

Keep each bullet atomic and understandable without reading the full log. Put
the first blocking failure first, but list every other failure that was actually
observed. Distinguish failures from checks that were not run or were
interrupted. Do not claim success from partial verification.

## Rollback

If post-install validation fails, restore the immediately previous retained
plugin version and repoint the stable runtime atomically through normal plugin
provisioning. Start a new task for any registration or skill rollback; fully
reopen Codex only if the catalog/cache is stale or the new task still fails.
Do not edit Wiki or user configuration files to roll back a package.
