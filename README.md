# FreeCAD Codex Sidebar

**Development alpha.** Contributions and reproducible bug reports are welcome.

A companion for [FreeCAD AI](https://github.com/ghbalf/freecad-ai) that connects
its native CAD tools to an installed, signed-in Codex runtime. Authentication
stays with Codex, including its existing ChatGPT subscription access and usage
limits. No separate OpenAI API key is required by this bridge.

The sidebar binds each task to an explicit document, output folder and source
files. It shows the selected model and reasoning effort, execution phase and
reported token usage. New tasks start fresh conversations; Stop invalidates
queued CAD calls, and Resume requires inspection of the retained state.

## Workflow

1. Open the intended target document and the FreeCAD AI dock.
2. Choose **Target document**, an existing **Workspace**, and any **Sources**
   to preserve. Select a model profile: Economical, Standard or Complex.
3. Enter a bounded task. Use Plan for discussion or Act for native tools.
4. Act saves a checkpoint copy before execution. The task progresses through
   Build → Check → Repair when needed → Check → Saved.
5. After Stop or failure, inspect the document, use **Inspect / reconcile**,
   then **Resume**. Cancellation preserves edits already made; it is not Undo.

A worker returning text is not a passed geometry check. The panel keeps that
result pending inspection. The Saved phase requires explicit check evidence
and a native save receipt: unchanged content revision and file hash, saved
state, no touched content and no pending transaction.

## Install from this checkout

Requirements: FreeCAD with the separate FreeCAD AI add-on installed, and a
working Codex desktop/CLI installation signed in to the intended account.
The native adapter has been exercised with FreeCAD 1.1.4 on Windows. Other
platforms and add-on versions need their own native verification.

Use the existing folder reported by `FreeCAD.getUserAppDataDir()`:

```powershell
python tools/install.py --user-dir "<FreeCAD user directory>"
python tools/install.py --user-dir "<FreeCAD user directory>" --apply
```

The first command previews the file list. Apply backs up existing matching
files under `CodexSidebarBackups/<timestamp>` and installs the companion in
`Mod/ArthurCodexCompanion`. Save documents and restart FreeCAD to load it.
The installer does not alter the upstream add-on or remove unrelated files.

For a previous companion installation, restore the backed-up `InitGui.py`
and any other replaced files with FreeCAD closed. Retain the backup manifest.
New companion files can remain unused when the restored entry point points
to the previous installation.

Runtime state and the loopback bearer token live outside this repository.
Optional environment overrides are `FREECAD_CODEX_BINARY` and
`FREECAD_CODEX_STATE_DIR`. Never place authentication data in source control.

## Checks

```powershell
python -m unittest discover -s tests -q
```

The automated tests exercise cancellation, target revisions, source changes,
phase transitions, transport continuation, permissions, usage deltas and
installation backups without inference. `tests/native_controller_fixture.py`
adds a manual native test using two temporary documents. It checks actual
FreeCAD observer callbacks, save-copy behavior and transaction rollback;
it does not run a model or edit existing project documents.

See [verification scope](docs/verification.md) for the development baseline
and the limits of the live test.

## Limits

The controller coordinates one sidebar writer. It is not a sandbox for Python
or a lock against another independent CAD client. In-process Resume retains
the current conversation; automatic recovery after a process restart is not
implemented. Persisted status is evidence, not a restorable live session.

Usage distinguishes cached, uncached and output tokens where Codex reports
them. This is not a billing calculator or a demonstrated savings claim.
CAD checks do not establish physical fit, fabrication acceptance or approval.

See [acknowledgements](ACKNOWLEDGEMENTS.md),
[feature sources and adoption decisions](docs/feature-adoption.md) and
[dependency notices](NOTICE.md). Fusion and SolidCraft interaction patterns
are references; their source code and artwork are not bundled.

The [expanded landscape review](docs/landscape-research.md) compares direct
alternatives including ChatCAD and iplayfast's FreeCAD AI Assistant. This
companion is one experimental approach; chat and native CAD tools already
exist in those projects and in the upstream workbench.

## Contributing and supporting development

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Small, tested improvements and
native compatibility reports are useful, especially around stop/resume,
document targeting and reliable saved-file checks.

The [sponsorship plan](docs/SPONSORSHIP.md) describes possible funding for
maintenance and model testing. Funding is not active yet; no donation or
payment link has been enabled.
