# Verification baseline, 26 October 2026

Development alpha tested on Windows with FreeCAD 1.1.4, FreeCAD AI
`08d637d145dbc12397599aedafa93ada2dd35f92` and Codex runtime
`0.164.0-alpha.11.1`.

- **70 automated tests passed** without model inference. They cover transport,
  task budgets, cancellation, continuation, permissions, target revisions,
  source/output bounds, save receipts and installer backups.
- **Native observer fixture passed:** recompute notifications, external
  property/Shape changes, unrelated documents, target switching, queued-call
  cancellation, checkpoint copy and actual transaction rollback.
- **Metadata handshake passed:** existing ChatGPT authentication and selected
  model profiles were available; the app-server returned the exact requested
  workspace. Metadata availability alone does not prove every model entitlement.
- **One live model smoke run** created and measured a native 10 × 20 × 30 mm
  Part Box with one valid solid and volume 6000 mm³, then saved it. It exposed
  a false Saved failure because the GUI dirty flag remained set. The run was
  stopped; a clean model-generated final completion was not observed.
- **After the fix**, a native test exercised the actual save/phase functions,
  rejected an edit followed by recompute without resaving, accepted a new save,
  and reopened a separate saved copy with identical measured geometry. This
  repair verification added no model turn.

The GUI dirty flag is retained as a diagnostic. Saved acceptance checks the
native save receipt and content state instead. Native tests ran on isolated
fixtures; private project geometry and raw account/session logs are excluded
from this repository.

Restart recovery, other operating systems, complex production CAD and physical
fit remain unverified. The CI workflow tests the Python components on its
listed platforms; it does not launch FreeCAD or a paid model.


## Task tool profiles — 31 October 2026

Reviewed upstream sidebar `main` at `b4b97548d25d3812fa84637871ea8411b01ea694`;
no task-specific profiles were implemented there. The existing 70-test suite
passed before changes in Linux / CPython 3.12.14.

The profile implementation passes **88 automated tests** (18 added), using
synthetic registries, Qt/native stand-ins, temporary files and a mock Codex
client. Command: `python -m unittest discover -s tests -q`. These tests make no
inference calls. `git diff --check` also passes.

A separate compatibility check loaded the actual `ToolRegistry` and evaluated
only `ToolDefinition` declarations (handlers replaced with `None`) from FreeCAD
AI `08d637d145dbc12397599aedafa93ada2dd35f92`. Each profile serialized and
round-tripped through the frozen contract: Inspect 8, Sketch 12, Solid 26,
Parameters 14 and Export 10 tools, including controls. This checks the pinned
schema interface, not FreeCAD imports, native handlers or geometry.

No FreeCAD application is installed in this execution environment. The native
observer fixture, Qt UI, new profiles against actual CAD, and save/reopen checks
were **not rerun** for this change. The October 26 native results above remain
historical evidence only. See [native follow-up scenarios](task-tool-profiles.md#verification-and-native-follow-up).
No measured token savings, production readiness or native compatibility beyond
the previously recorded installation is claimed.
