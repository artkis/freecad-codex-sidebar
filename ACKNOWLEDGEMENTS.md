# Acknowledgements

This project benefits from the work and documentation of these communities and
products. The relationships below distinguish runtime dependencies from design
ideas; acknowledgement does not imply that a feature has been implemented.

| Source | Contribution and relationship |
| --- | --- |
| [FreeCAD](https://www.freecad.org/) | Native CAD application and Python/Qt environment. Installed separately. |
| [FreeCAD AI — ghbalf](https://github.com/ghbalf/freecad-ai) | Separately installed workbench whose interfaces the companion imports. Its Plan/Act workflow, selection references and structured tools also informed this integration. Code: LGPL-2.1-or-later; icons: CC0-1.0. Inspected commit: `08d637d145dbc12397599aedafa93ada2dd35f92`. |
| [SolidCraft — Tangency Labs and contributors](https://github.com/tangency-labs/solidcraft/blob/348bbc1dfa99a4444c6d15c49d0f9affdb6af0c0/docs/agent-chat.md) | Design inspiration for an agent sidebar, visible tool progress, cancellation/steering, bounded work, viewport context and grouped undo. Research reference only; no SolidCraft source or assets copied. Repository code offers MIT OR Apache-2.0, subject to its third-party notices. |
| [Autodesk Fusion / Autodesk Assistant](https://help.autodesk.com/view/fusion360/ENU/?contextid=LEARNINGPANEL) | Public documentation informed the distinction between explaining, inspecting, guiding and acting on a CAD task. Idea reference only; no Autodesk software, source or assets included. |
| [freecad-mcp — neka-nat and contributors](https://github.com/neka-nat/freecad-mcp/tree/8babe9927ce8e6e8fe7dd6ae2ffa2c93776e845a) | Research reference for a separate native addon/client bridge. MIT; no code copied into this repository. |
| [freecad-mcp — yonosoft and contributors](https://github.com/yonosoft/freecad-mcp/tree/ffdd89673887acd0c2c7c7261fb7df8e1880ff06) | Research reference for typed operations, solver evidence and bounded verification/rollback contracts. LGPL-2.1-or-later; no code copied or dependency bundled. |
| [Codex ACP — Agent Client Protocol contributors](https://github.com/agentclientprotocol/codex-acp/tree/a77829fecc87cebbb2e53782ce6795aee072adc8) | Research reference for connecting an existing Codex login to an application client. Apache-2.0; the companion uses Codex App Server directly and does not bundle this adapter. |

The companion's own code is MIT licensed. Upstream licenses and notices remain
applicable to their own projects; see [NOTICE.md](NOTICE.md) and the
[feature research ledger](docs/feature-adoption.md) for provenance and limits.

FreeCAD Codex Sidebar is an independent project. It is not affiliated with,
sponsored by or endorsed by FreeCAD, Tangency Labs, Autodesk, OpenAI or the other
projects listed above. Product names and trademarks belong to their respective
owners. No third-party logos or artwork are included.
