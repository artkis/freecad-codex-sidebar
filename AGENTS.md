# Development contract

Keep task ownership, exact target document, authorized workspace and source
references explicit. One controller owns live CAD writes. Preserve original
documents and treat tool execution, geometry validity and saved-file validation
as separate outcomes.

Use deterministic tests before model calls. A regression test must exercise a
real failure boundary: targeting, permissions, cancellation, state transitions,
token deltas, continuation or persistence. Never run usage-consuming model
probes unless the active task requires them.

Use native FreeCAD APIs and MCP. Avoid desktop mouse/keyboard automation.
Never commit credentials, bearer tokens, account data, private project geometry,
conversation dumps, machine-specific paths or local settings.

Keep dependencies separate. Inspect upstream licenses before copying code.
Document verified feature support and limitations; research is not a test pass.
