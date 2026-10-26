# Contributing

This is an early companion for FreeCAD AI. Open an issue with the behavior you
expected, what happened, and the smallest reproduction you can share.
Include FreeCAD, FreeCAD AI, Codex and operating-system versions.

Use a synthetic CAD fixture. Remove account details, tokens, private paths,
source drawings and proprietary project geometry before posting logs or files.

For changes:

1. Explain the concrete failure or missing capability.
2. Keep the patch small and add a regression test for the failure boundary.
3. Run `python -m unittest discover -s tests -q` without a model or live CAD app.
4. For native changes, run an isolated FreeCAD fixture and report what was
   checked, what was saved/reopened, and what remains untested.

Do not make tests consume a contributor's model quota by default. Live model
tests need an explicit opt-in and bounded time/tool budgets. A mock test is
not proof of native geometry or GUI callback behavior.

Before reusing third-party code, verify its license, pin its source revision
and retain the required notices. Keep inspiration credits separate from
code dependencies. Contributions are made under this repository's MIT license
unless a file explicitly states another compatible license and its origin.

Useful areas for development include recovery after restart, more native
fixtures, bounded selection context, cross-platform installation and a clearer
task-progress display. Discuss major transport or architecture changes first.
