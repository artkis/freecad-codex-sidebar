"""Manual native proof, deliberately separate from ordinary unit tests.

Inside an idle FreeCAD GUI console (no model worker), add src/freecad_codex
and tests to sys.path, then import this module and call run_fixture with an
explicit private output directory. It creates only uniquely named fixture
documents and never calls an existing companion or an LLM. It closes its own
documents and restores the prior App/GUI active documents. Keep the returned
JSON as proof; a failure is a real compatibility finding, not a pass.
"""
from pathlib import Path
import json
import time
import uuid


def run_fixture(output_directory):
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    from task_controller import TaskController, GuardError
    from document_guard import DocumentObserver

    output = Path(output_directory).resolve(strict=True)
    if not output.is_dir():
        raise ValueError("Supply an existing private fixture output directory")
    previous_app = App.ActiveDocument.Name if App.ActiveDocument else None
    previous_gui = Gui.activeDocument().Document.Name if Gui.activeDocument() else None
    tag = uuid.uuid4().hex[:10]
    created = []
    proof = {"fixture": tag, "checks": {}, "native_edits_confined_to": []}
    controller = TaskController()
    observer = DocumentObserver(controller)
    App.addDocumentObserver(observer)
    try:
        target = App.newDocument("GuardFixture_" + tag)
        created.append(target.Name)
        other = App.newDocument("OtherFixture_" + tag)
        created.append(other.Name)
        proof["native_edits_confined_to"] = list(created)
        box = target.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 10, 20, 30
        direct = target.addObject("Part::Feature", "DirectShape")
        direct.Shape = Part.makeBox(1, 2, 3)
        target.recompute()
        App.setActiveDocument(target.Name)
        Gui.activeDocument().activeView().viewAxonometric()
        controller.bind(target.Name)
        payload = {"target_document": target.Name, "workspace": str(output),
                   "expected_revision": controller.revision.token,
                   "prompt": "Harmless isolated native guard fixture"}
        task = controller.start(payload, "fixture-no-model", "none")
        def names():
            return App.ActiveDocument.Name, Gui.activeDocument().Document.Name
        controller.check(*names())
        before = controller.revision.token
        target.recompute()  # Mimics the native add-on's read-only code wrapper.
        controller.check(*names())
        proof["checks"]["read_only_recompute_neutral"] = controller.revision.token == before
        box.touch()
        target.recompute()
        controller.check(*names())
        proof["checks"]["touched_recompute_neutral"] = controller.revision.token == before
        box.Length = 11
        try:
            controller.check(*names())
            raise AssertionError("External native input was not detected")
        except GuardError:
            proof["checks"]["external_input_stale"] = True
        controller.reconcile(*names())
        controller.resume(*names())
        direct.Shape = Part.makeBox(3, 4, 5)
        target.recompute()
        try:
            controller.check(*names())
            raise AssertionError("External direct Shape edit was not detected")
        except GuardError:
            proof["checks"]["direct_shape_edit_stale"] = True
        controller.reconcile(*names())
        controller.resume(*names())
        other.addObject("Part::Box", "UnrelatedBox")
        other.recompute()
        controller.check(*names())
        proof["checks"]["unrelated_document_ignored"] = True
        App.setActiveDocument(other.Name)
        try:
            controller.check(*names())
            raise AssertionError("Target switch was not detected")
        except GuardError:
            proof["checks"]["target_switch_stale"] = True
        App.setActiveDocument(target.Name)
        controller.reconcile(*names())
        controller.resume(*names())
        generation = task.generation
        controller.stop()
        try:
            controller.check(*names(), generation=generation)
            raise AssertionError("Cancelled queued write was not blocked")
        except GuardError:
            proof["checks"]["cancel_blocks_queued_generation"] = True
        # Checkpoint is a copy; saveCopy must preserve the existing document path.
        checkpoint = output / ("fixture-" + tag + ".FCStd")
        original_filename = target.FileName
        target.saveCopy(str(checkpoint))
        assert checkpoint.is_file() and checkpoint.stat().st_size > 0
        assert target.FileName == original_filename
        proof["checks"]["checkpoint_copy_preserves_target_path"] = True
        proof["checkpoint"] = str(checkpoint)
        # Verify real transaction rollback on this fixture, not merely its return.
        target.UndoMode = 1
        original_length = float(box.Length)
        target.openTransaction("Isolated rollback proof")
        box.Length = 17
        target.abortTransaction()
        target.recompute()
        assert float(box.Length) == original_length
        proof["checks"]["transaction_abort_restores_length"] = True
        proof["ok"] = all(proof["checks"].values())
    except Exception as exc:
        proof["ok"] = False
        proof["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        App.removeDocumentObserver(observer)
        for name in reversed(created):
            App.closeDocument(name)
        if previous_gui and App.getDocument(previous_gui):
            App.setActiveDocument(previous_gui)
        if previous_app and App.getDocument(previous_app):
            App.setActiveDocument(previous_app)
        proof["restored_app"] = App.ActiveDocument.Name if App.ActiveDocument else None
        proof["restored_gui"] = Gui.activeDocument().Document.Name if Gui.activeDocument() else None
        proof["checked_at"] = time.time()
        (output / ("fixture-" + tag + ".json")).write_text(json.dumps(proof, indent=2), encoding="utf-8")
    return proof
