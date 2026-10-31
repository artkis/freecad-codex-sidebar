# Sidebar feature adoption — 26 October 2026

See the [expanded landscape review](landscape-research.md) for direct FreeCAD
assistant comparisons, a wider candidate inventory and revised priorities.

Research scope: improve the FreeCAD Codex companion using documented CAD-assistant patterns and inspected open-source components. No live CAD changes, package installation, or third-party code copying occurred in this research. “Adopt now” means the next implementation priority, not a completed feature.

## What was verified

The installed `ghbalf/freecad-ai` source is commit `08d637d145dbc12397599aedafa93ada2dd35f92`, package **0.33.0-alpha**. Its actual `core/references.py`, tool transaction wrapper, and package licenses were read. Public search metadata was older, so the inspected checkout governs these observations. Other projects below were reviewed through their official repository/documentation and actual license files; their local runtime behavior was not tested.

Fusion's current official Help describes four assistance levels: explanation, design inspection, guided work, and approved actions. Design edits belong to Tech Preview; this does not establish availability in every installed build or a callable assistant-chat interface through MCP. [Fusion Help](https://help.autodesk.com/view/fusion360/ENU/?contextid=LEARNINGPANEL)

## Adoption ledger

| Feature / source | License / verified boundary | Value and decision |
| --- | --- | --- |
| Exact source and target banner; selected object/face/edge references. [FreeCAD AI reference source](https://github.com/ghbalf/freecad-ai/blob/08d637d145dbc12397599aedafa93ada2dd35f92/freecad_ai/core/references.py) | [Code LGPL-2.1-or-later](https://github.com/ghbalf/freecad-ai/blob/08d637d145dbc12397599aedafa93ada2dd35f92/package.xml); icons separately CC0. Source snapshots internal object names and subelements with concise geometric descriptions. | **Now:** reimplement a compact reference packet with document/session identity, source path, revision, units, camera and selected-object descriptors. Revalidate before acting. A face index is state-specific, not permanent topology identity. Avoid whole-document scans. |
| Plan / Act / Check separation. [FreeCAD AI](https://github.com/ghbalf/freecad-ai) and [Fusion Help](https://help.autodesk.com/view/fusion360/ENU/?contextid=LEARNINGPANEL) | LGPL for FreeCAD AI code; Autodesk product documentation is an idea reference, not reusable code/assets. | **Now:** show intent, allowed objects and checks before execution. Check means isolated execution plus native readback; distinguish it from executing against the live document. Keep a single live writer. |
| Stop, queued steering, progress/tool cards, attach viewport, one undo per message. [SolidCraft agent chat](https://github.com/tangency-labs/solidcraft/blob/main/docs/agent-chat.md) | Actual [MIT](https://github.com/tangency-labs/solidcraft/blob/main/LICENSE-MIT) and [Apache-2.0](https://github.com/tangency-labs/solidcraft/blob/main/LICENSE-APACHE) files verified; own code is dual licensed, third-party assets retain their licenses. Documentation describes behavior; not a local regression test. | **Now:** deterministic running/stopping/finished states and queued correction; wait for stop acknowledgement before another writer. Show bounded tool progress. **Later:** grouped undo and dock/pop-out polish after native transaction proof. |
| Bounded execution and user-owned agent login. [SolidCraft agent chat](https://github.com/tangency-labs/solidcraft/blob/main/docs/agent-chat.md), [Codex ACP](https://github.com/agentclientprotocol/codex-acp) | Codex adapter [Apache-2.0](https://github.com/agentclientprotocol/codex-acp/blob/main/LICENSE). README supports ChatGPT authentication and model/effort configuration. It is an adapter, not a CAD kernel. | **Now:** keep the working subscription route; explicit step/time/context budgets and incremental event reads. No new API billing requirement. **Later:** evaluate ACP transport only if it simplifies existing Codex integration. No claim of zero usage limits or measured token savings. |
| Separate CAD addon and MCP client. [neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp) | Actual [MIT license](https://github.com/neka-nat/freecad-mcp/blob/main/LICENSE) verified. Repository documents inspection, modeling, Python and FEM through two components. | **Later:** targeted reuse of an inspected serialization/client helper if a concrete gap appears; retain notice and pin revision. **Reject:** installing a second competing CAD bridge or broad arbitrary execution merely to gain features already available. |
| Typed operations, solver facts, verified deltas and rollback contracts. [yonosoft/freecad-mcp](https://github.com/yonosoft/freecad-mcp) | [LGPL-2.1-or-later](https://github.com/yonosoft/freecad-mcp/blob/main/LICENSE). Repository explicitly limits current evidence mainly to bounded Sketcher workflows. | **Now:** adopt the verification pattern: recompute, shape/solver checks, measured intended change, unchanged protected sources. **Later:** test selected typed Sketcher operations on fixtures. **Reject:** treating its experimental benchmark as general 3D capability. |

## Small reusable component to integrate first

Implement an independent `ReferencePacket` serializer rather than copying an upstream workbench: explicit document key + expected revision + object internal names + optional subelement + units + bounded shape summary + camera snapshot + capture timestamp. Selection is an explicit attachment; active-tab changes never silently retarget it. This is a new local component inspired by the inspected FreeCAD AI reference design; no upstream bytes need distribution. Existing LGPL workbench modules can remain separate and unmodified. If code is later copied, pin its commit, preserve its applicable license/notices and record the exact copied files.

Acceptance: two open fixture documents with duplicate labels; correct target remains bound after tab switch; label change preserves object reference; topology mutation makes stale subelements fail closed; packet size stays bounded; no geometry mutation occurs during capture. A camera image supports context and visual checking but does not prove dimensions or physical fit.

## Transaction and verification caution

The inspected FreeCAD AI `_with_undo` resolves the active document and opens/commits/aborts a transaction. Its variable-set code separately warns that abort is ineffective with `UndoMode == 0` and explicitly removes a partially created object. Reusing the wrapper alone would preserve neither exact targeting nor guaranteed rollback. Test enabled undo, failure rollback, unrelated user edits, save/reopen and cancellation on an owned fixture before offering grouped undo. [Tool implementation](https://github.com/ghbalf/freecad-ai/blob/08d637d145dbc12397599aedafa93ada2dd35f92/freecad_ai/tools/freecad_tools.py)

Final status should separate transport success, CAD operation success, independent geometry checks, persisted checkpoint and remaining limitations. No SolidCraft or Fusion source code or assets were copied. FreeCAD AI remains a separately installed LGPL dependency. See [acknowledgements](../ACKNOWLEDGEMENTS.md) and [dependency notice](../NOTICE.md).

## Pinned license references

Licenses were checked from primary source files on 26 October 2026. These pins record the reviewed material, not a promise about future upstream changes:

- FreeCAD AI `08d637d145dbc12397599aedafa93ada2dd35f92`: [package license declaration](https://github.com/ghbalf/freecad-ai/blob/08d637d145dbc12397599aedafa93ada2dd35f92/package.xml), [LGPL text](https://github.com/ghbalf/freecad-ai/blob/08d637d145dbc12397599aedafa93ada2dd35f92/LICENSE-CODE).
- SolidCraft `348bbc1dfa99a4444c6d15c49d0f9affdb6af0c0`: [MIT](https://github.com/tangency-labs/solidcraft/blob/348bbc1dfa99a4444c6d15c49d0f9affdb6af0c0/LICENSE-MIT), [Apache-2.0](https://github.com/tangency-labs/solidcraft/blob/348bbc1dfa99a4444c6d15c49d0f9affdb6af0c0/LICENSE-APACHE).
- neka-nat/freecad-mcp `8babe9927ce8e6e8fe7dd6ae2ffa2c93776e845a`: [MIT](https://github.com/neka-nat/freecad-mcp/blob/8babe9927ce8e6e8fe7dd6ae2ffa2c93776e845a/LICENSE).
- yonosoft/freecad-mcp `ffdd89673887acd0c2c7c7261fb7df8e1880ff06`: [LGPL-2.1-or-later](https://github.com/yonosoft/freecad-mcp/blob/ffdd89673887acd0c2c7c7261fb7df8e1880ff06/LICENSE).
- Codex ACP `a77829fecc87cebbb2e53782ce6795aee072adc8`: [Apache-2.0](https://github.com/agentclientprotocol/codex-acp/blob/a77829fecc87cebbb2e53782ce6795aee072adc8/LICENSE).


## SolidCraft-to-FreeCAD implementation evidence — 31 October 2026

Direction: adapt the interaction and control behavior to FreeCAD's existing
Python + Qt/PySide sidebar and native document APIs. Do not port SolidCraft's
Rust engine, replace FreeCAD's kernel, or introduce another model provider.
This pass implements task-specific tool profiles, not a complete visual/UI
rebuild, queued steering or grouped undo.

Focused source review uses SolidCraft commit
`045cfed6a7afc61c96bcf8169a634d37ba7d9479`:

| Observed source | Adaptation / evidence limit |
| --- | --- |
| [agent_chat/gate.rs](https://github.com/tangency-labs/solidcraft/blob/045cfed6a7afc61c96bcf8169a634d37ba7d9479/crates/ui-egui/src/agent_chat/gate.rs) has a method allowlist and a command verdict before dispatch, including file/design/undo bounds. | Keep tool advertising separate from enforcement. FreeCAD's gate checks explicit target, workspace, revision and frozen task permissions; no claim that our policies are identical. |
| [agent_chat/context.rs](https://github.com/tangency-labs/solidcraft/blob/045cfed6a7afc61c96bcf8169a634d37ba7d9479/crates/ui-egui/src/agent_chat/context.rs) builds bounded selection context with item kinds and omission counts. | Retain FreeCAD's explicit document/reference packet. A fuller selection-chip UI remains future work; static source does not prove native behavior. |
| [mcp/tools.rs](https://github.com/tangency-labs/solidcraft/blob/045cfed6a7afc61c96bcf8169a634d37ba7d9479/crates/mcp/src/tools.rs) separates inspection tools from commands and includes selective command discovery. | Use typed FreeCAD tools in explicit task profiles. This pass adds no broad execute-command dispatcher and does not claim SolidCraft implements these same profiles. |
| [agent-chat.md](https://github.com/tangency-labs/solidcraft/blob/045cfed6a7afc61c96bcf8169a634d37ba7d9479/docs/agent-chat.md) describes per-design context, Stop/queued steering, activity rows and own-account runtime use. | Existing account, ownership and Stop/Resume route retained. Activity-card polish and queued steering are not implemented by this patch. |

The pinned MIT and Apache-2.0 licenses were inspected. No SolidCraft source
code or artwork was copied; this is an independent Python implementation of
selected behavior. The upstream FreeCAD AI workbench remains a separate LGPL
dependency. Frozen task-specific profiles also follow the direct FreeCAD
comparison in the landscape review, not an invented SolidCraft capability.

REA identity was resolved to [morluto/rea](https://github.com/morluto/rea).
Its [workflow skill at d48e472](https://github.com/morluto/rea/blob/d48e472fa6a664cacbb400560e673533005795ce/.agents/skills/reverse-engineer-anything/SKILL.md)
routes ordinary complete-source analysis to normal repository tools; native
REA analysis is for shipped artifacts or runtime evidence beyond available
source. This pass follows that routing and source/evidence separation. No REA
MCP runtime was available or invoked here, and no installed desktop plugin was
changed. This is source inspection, not binary or live-app REA verification.
