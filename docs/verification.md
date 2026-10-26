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
