# Wiki Preflight Repository Instructions

## Plugin release gate

Before any plugin version change, release preparation, marketplace upgrade,
cache cleanup, commit intended for release, push intended for release, or
post-install validation, read and follow
[`docs/PLUGIN_RELEASE_SOP.md`](docs/PLUGIN_RELEASE_SOP.md) in full.

Do not mark a release complete until its required acceptance gate and
post-install validation pass. Never delete user configuration, Wiki content,
receipts, queues, or the active stable runtime while cleaning package cache.
