# CAD assistant landscape review, 26 October 2026

This review covers 18 open-source projects plus Autodesk's public Fusion
Assistant documentation, including direct FreeCAD assistant workbenches.
It is a bounded comparison, not an exhaustive survey or a claim that every
project was tested.

## Evidence and coverage

Discovery used GitHub searches for FreeCAD assistants, FreeCAD MCP, parametric
CAD agents, OpenSCAD agents and Fusion MCP. The FreeCAD add-on repository was
also checked, but its legacy `.gitmodules` list did not supply the relevant AI
entries; it was not treated as a complete current catalog.

Repository metadata and READMEs establish advertised scope and activity.
Focused source inspection was done for ghbalf, iplayfast, ChatCAD and our
companion. Other rows are discovery references or narrower feature research.
Commit dates do not establish software quality. License metadata is a routing
aid; the exact files and license must be checked before reuse.

No alternative workbench was installed into the live FreeCAD session, and no
comparative model benchmark was run. Our own bounded test results are in
[verification.md](verification.md).

## Closest product comparisons

| Project | Actual Codex access path | Useful features and boundaries found in source |
| --- | --- | --- |
| [ChatCAD](https://github.com/Vittuu/freecad-chatcad/tree/49d3cce25f878e08cf3f970e1ab1bca5d4604346) | Embedded `codex exec` / `exec resume --json`, streamed through Qt `QProcess`. | Closest direct sidebar comparison found. Expandable tool-step cards, selected object/subelement context and image annotation. Local-model recovery and CLI/MCP recovery are separate implementations; their guarantees must not be conflated. |
| [iplayfast FreeCAD AI Assistant](https://github.com/iplayfast/FreeCAD-AI-Assistant/tree/940084d36cca6677c44eba3150184f60ff087f6f) | Existing-subscription CLI runs in an external terminal; panel chat uses provider endpoints. | One registry for panel, console and MCP; selectable tool profiles, bounded results, operation lookup and typed inspection. Windows CLI launching and native cancellation need further work/testing. |
| [ghbalf FreeCAD AI](https://github.com/ghbalf/freecad-ai/tree/08d637d145dbc12397599aedafa93ada2dd35f92) + this companion | Embedded Codex App Server connection supplied by the companion. | Upstream supplies the dock, chat, references and native tools. The companion adds task ownership, revision checks, queued-call cancellation, budgets and save receipts. Qt method hooks depend on the inspected upstream interface. |

### ChatCAD: separate execution paths matter

At the pinned revision, `chatcad_widgets.py` contains `StepRow` and `StepsGroup`;
`chatcad_panel.py` builds selected-reference context; `chatcad_sketch.py`
implements image annotation. Annotation strokes are image context, not native
CAD geometry. [Source tree](https://github.com/Vittuu/freecad-chatcad/tree/49d3cce25f878e08cf3f970e1ab1bca5d4604346)

The CLI adapter is implemented in
[chatcad_backend.py](https://github.com/Vittuu/freecad-chatcad/blob/49d3cce25f878e08cf3f970e1ab1bca5d4604346/chatcad_backend.py).
Its Codex path delegates to the separate neka-nat MCP bridge. Backup, active
document and geometry rules in that lane are model instructions. The Plan
instruction does not remove mutation tools, and process termination alone
does not establish that an already-dispatched native CAD action has stopped.

The native transactions, backup and rollback logic in
[chatcad_fctools.py](https://github.com/Vittuu/freecad-chatcad/blob/49d3cce25f878e08cf3f970e1ab1bca5d4604346/chatcad_fctools.py)
belong to the in-process local-model lane. Its backup copies an existing disk
file; unsaved live edits need separate preservation. These are reasons to test
each lane, not conclusions about the project's overall quality.

### iplayfast: useful inspection and context design

[inspect_handlers.py](https://github.com/iplayfast/FreeCAD-AI-Assistant/blob/940084d36cca6677c44eba3150184f60ff087f6f/freecad/AIAssistant/rpc_server/inspect_handlers.py)
contains shape checks, mass properties, real shape-to-shape distance,
intersection volume, solver state and document-qualified selection.
These are concrete candidates for a typed inspection interface.

[agent.py](https://github.com/iplayfast/FreeCAD-AI-Assistant/blob/940084d36cca6677c44eba3150184f60ff087f6f/freecad/AIAssistant/rpc_server/agent.py)
selects advertised tools, caps serialized results and detects repeated calls
or errors. Its profiles are not permission boundaries: the common registry
still accepts tools by name, and the selector includes arbitrary Python.
Our enforced allowlist must remain separate from context reduction.

Its operation map provides selective API examples and failure hints. Retrieving
one relevant recipe is preferable to injecting the entire map. The shared
registry also reduces differences between access surfaces.
[Operation lookup](https://github.com/iplayfast/FreeCAD-AI-Assistant/blob/940084d36cca6677c44eba3150184f60ff087f6f/freecad/AIAssistant/rpc_server/bim_handlers.py)

Source inspection also found that clearing panel events does not cancel the
old agent loop, and the CLI wrapper uses Unix-specific facilities before its
platform fallback. These findings need isolated native reproduction before
being reported as runtime failures.
[Panel and launcher source](https://github.com/iplayfast/FreeCAD-AI-Assistant/tree/940084d36cca6677c44eba3150184f60ff087f6f/freecad/AIAssistant)

## Wider candidate inventory

Dates below are the latest commit metadata observed in this review. Licenses
in this table are reported repository metadata unless the pinned license was
separately inspected in the [feature ledger](feature-adoption.md).

| Project | Activity / reported license | Relevance and evidence limit |
| --- | --- | --- |
| [ghbalf/freecad-ai](https://github.com/ghbalf/freecad-ai) | Pinned source inspected / LGPL-2.1-or-later; separate CC0 icons | Current dependency; actual native integration exercised. |
| [iplayfast/FreeCAD-AI-Assistant](https://github.com/iplayfast/FreeCAD-AI-Assistant) | 2026-10-24 / LGPL-2.1-or-later | Direct assistant; focused code review, no runtime comparison. |
| [Vittuu/freecad-chatcad](https://github.com/Vittuu/freecad-chatcad) | 2026-10-12 / LGPL-2.1 package declaration | Direct embedded Codex comparison; focused code review. |
| [neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp) | Pinned license inspected / MIT | Separate native bridge; external model/client required. |
| [yonosoft/freecad-mcp](https://github.com/yonosoft/freecad-mcp) | Pinned license inspected / LGPL-2.1-or-later | Typed operations and bounded Sketcher verification research. |
| [CREATeNG/freecad-mcp-bridge](https://github.com/CREATeNG/freecad-mcp-bridge) | 2026-10-26 / MIT | Explicit server connect/off state; discovery only. |
| [gurul/gencad](https://github.com/gurul/gencad) | 2026-10-23 / MIT | Headless FreeCAD with section/render feedback; discovery only. |
| [blwfish/freecad-mcp](https://github.com/blwfish/freecad-mcp) | 2026-10-23 / LGPL-2.1 | Structured FreeCAD/CAM/mesh tools; discovery only. |
| [tessalabs-space/freecad-mcp](https://github.com/tessalabs-space/freecad-mcp) | 2026-10-21 / metadata `NOASSERTION`, README says MIT | Broader CAD/drafting/CAE scope; license unresolved for reuse. |
| [Getopir/AgentCAD](https://github.com/Getopir/AgentCAD) | 2026-07-31 / LGPL-2.1 | Separate FreeCAD-derived platform, typed gateway; pre-alpha claims, no runtime review. |
| [seansackowitz/mcp-freecad](https://github.com/seansackowitz/mcp-freecad) | 2026-05-29 / MIT | Narrow native tools and optional screenshots; discovery only. |
| [sergiudanstan/freecad-mcp](https://github.com/sergiudanstan/freecad-mcp) | 2026-03-08 / MIT | Node MCP with broad GUI/headless coverage; discovery only. |
| [toreleon/CADAgent](https://github.com/toreleon/CADAgent) | 2026-04-29 / LGPL-2.1 | Separate FreeCAD distribution with API/local-proxy assistant; not our existing-subscription route. |
| [tangency-labs/solidcraft](https://github.com/tangency-labs/solidcraft) | Pinned licenses inspected / MIT OR Apache-2.0 | Separate CAD product; chat/steering/undo interaction references. |
| [Kevoyuan/AgentSCAD](https://github.com/Kevoyuan/AgentSCAD) | 2026-10-25 / MIT | OpenSCAD pipeline and output validation; not a FreeCAD backend. |
| [barisgit/fusion360-bridge](https://github.com/barisgit/fusion360-bridge) | 2026-07-15 / license metadata absent | Fusion API/MCP discovery; no source reuse until license is resolved. |
| [CenturyMesh/Ark](https://github.com/CenturyMesh/Ark) | 2026-10-21 / Apache-2.0 | Separate OCCT CAD platform with CLI/MCP; different provider integration. |
| [agentclientprotocol/codex-acp](https://github.com/agentclientprotocol/codex-acp) | Pinned license inspected / Apache-2.0 | Alternative Codex adapter; evaluate only if it reduces transport maintenance. |

Autodesk's [Fusion Assistant documentation](https://help.autodesk.com/view/fusion360/ENU/?contextid=LEARNINGPANEL)
is an additional product-design reference, not an open-source dependency or
proof of availability on a particular installation.

## Development decisions

Keep the published companion as an experimental implementation while evaluating
the direct alternatives. This review does not establish that it is the best
base. ChatCAD deserves a comparative fixture before any broader UI rebuild.

Priorities for the next development pass:

1. **Stable task tool profiles.** Our current default exposes 51 tools. Select
   the smallest useful set before starting a task and keep its schema stable
   across Resume. Enforce scope independently of what is advertised.
2. **Typed, bounded inspection.** Add document-qualified shape, solver,
   clearance and interference results. Distinguish executed commands, measured
   checks and persisted output. A model's `checks_passed` assertion alone is
   not independent engineering validation. Revalidate subelement references
   against the content revision after agent-owned topology changes too.
3. **Readable tool-step cards.** Show action, target, result, error and checkpoint
   with expandable details. Reuse the existing dock before adding another UI.
4. **Loop and context limits.** Detect repeated identical calls/errors and cap
   results. Retrieve operation recipes on demand. Keep images optional.
5. **Recovery and integration boundaries.** Test stop acknowledgement across
   native calls, preserve unsaved state, and design restart recovery before
   claiming resumable sessions across application restarts. Replace fragile
   method hooks with an upstream extension interface where possible.

A comparative fixture should use the same synthetic task and measure actual
native editability, stop behavior, failure recovery, saved/reopened geometry,
elapsed time and cached/uncached/output tokens. No token-saving percentage or
performance winner is claimed without that evidence.

No source from the newly reviewed projects has been copied. LGPL components
cannot be silently relabeled MIT. Any future reused files need their original
license, provenance and distribution obligations preserved.
