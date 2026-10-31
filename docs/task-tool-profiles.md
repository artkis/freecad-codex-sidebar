# Task-specific CAD tools

The CAD tools selector controls the operations advertised for a **new** task.
It is separate from Economical / Standard / Complex model and effort settings.
Choose operations from the actual task requirements; there is no keyword
classifier that infers permission from a prompt.

| Profile | Intended work | Advertised tools including task controls |
| --- | --- | --- |
| `inspect` (default) | Target state, geometry measurements, faces/edges, parameter reads | 8 |
| `sketch` | Inspection plus body/sketch creation and sketch edits | 12 |
| `solid` | Sketch-based solids, primitives, booleans, transforms, fillets/chamfers, linear/polar patterns | 26 |
| `parameters` | Inspection plus variable sets, spreadsheets and expressions | 14 |
| `export` | Inspection plus model export | 10 |
| `custom` (submission API) | An explicit nonempty list of required tools | List size + 2 controls |

Counts are for the pinned FreeCAD AI contract used in the October 26 baseline.
They are schema inventory counts, **not measured token savings**. No comparative
inference benchmark has been run.

Act inspection includes `commander_phase` but no native save tool. Other Act
profiles include `commander_phase` and `commander_save`. Plan mode has no CAD
tools at all. Inspect Act still makes the existing pre-task checkpoint copy;
it does not promise an entirely file-write-free session. An inspection-only
task returns for user inspection rather than claiming Saved without a receipt.

For a synthetic sketch/pad task, a submission can narrow the Solid profile:

```python
payload.update({
    "tool_profile": "solid",
    "required_tools": ["create_body", "create_sketch", "edit_sketch",
                       "pad_sketch", "describe_model", "measure"],
})
```

The usual explicit target, revision, workspace, sources and acceptance criteria
remain required. Include the inspection and repair operations the task needs.
If the requested workflow needs another tool, stop/reconcile and start a new
task with that scope; Resume never widens the retained tool set. The UI offers
the five named profiles; exact lists and custom profiles use the submission API.
No change to the selected language or CAD kernel is needed: this implementation
is Python and Qt/PySide with the existing FreeCAD AI dependency.

## Permissions and ownership

`tool_profiles.py` selects an advertisement. `tool_policy.py` independently
determines permitted operations and validates structured arguments. Explicit
`allowed_tools`, when provided, retains its existing exact-allowlist meaning;
it is not an additive grant. `required_tools` cannot authorize arbitrary code,
macros, screenshots, document switching, user tools or background execution.
An opt-in tool needs both an explicit permission and a custom required-tool
selection. These trusted capabilities remain outside any Python sandbox claim.

Before dispatch, ToolGate checks the retained permission set **and** selected
names, current worker/generation, task settings, exact App/GUI target,
revision, source files, object ownership and output paths. A forged or widened
worker advertisement cannot grant permission. Unknown/missing required tools
are rejected; there is no fallback to the full registry. New upstream tools
are never automatically added to a named profile.

The controller remains a cooperative single sidebar writer, not an operating
system sandbox or a lock against another client. Existing source/checkpoint
protection and native save receipts remain in force.

## Stable Resume

Before provider startup/checkpoint creation, the task snapshots its settings,
selected names, permission set, canonical schemas and schema SHA-256. Worker
registries own copies of selected definitions and return detached, frozen
schemas in sorted name order. Registry insertion order and unrelated new tools
therefore do not change the retained conversation's advertised schema.

Resume preflights the current registry before resuming the provider. Removed
tools, a main-thread/permission change, changed selected schemas, or changed
task settings require a new task. Selected handler behavior is not hashed;
dependency upgrades still require a restart and fresh native qualification.
The provider's existing conversation/schema checks are retained as another
boundary. This supports in-process Resume only, not restart recovery.

## Verification and native follow-up

Regression tests cover profile/requirement selection, invalid inputs, missing
tools, explicit opt-in denial, frozen schema copies, order-independent Resume,
schema drift before provider continuation, Plan/Act changes, and rejection of
forged worker advertisements. A mock-provider integration test retains one
provider thread with actual supervisor-produced tool schemas across Stop and
Resume. Existing target, source, cancellation and saved-file tests remain.

On an installation with the pinned FreeCAD AI interface, run the existing
`tests/native_controller_fixture.py` on temporary documents. Then qualify the
new selector on synthetic data: Inspect cannot create a primitive; Solid can
create a 10 × 20 × 30 mm box and measure 6000 mm³; Stop/reconcile/Resume retains
the schema hash despite selecting another profile for the next task; switching
to a second document rejects queued operations. Save to a new fixture file,
reopen a copy and compare the native geometry. These new native scenarios have
**not** been run in the cloud Python environment.
