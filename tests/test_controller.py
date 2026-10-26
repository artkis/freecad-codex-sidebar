"""Guard behavior tests; do not import or start FreeCAD or the provider."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "freecad_codex"))
from task_controller import TaskController, GuardError
from document_guard import DocumentObserver


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.controller = TaskController()
        self.controller.bind("Target")
        self.observer = DocumentObserver(self.controller)
        self.doc = SimpleNamespace(Name="Target")
        self.obj = SimpleNamespace(Name="Box", Document=self.doc)
        self.payload = {"target_document": "Target", "workspace": str(self.workspace),
                        "prompt": "Build the authorized fixture", "expected_revision": self.controller.revision.token}

    def start(self, **overrides):
        return self.controller.start({**self.payload, **overrides}, "model", "medium")

    def test_explicit_target_workspace_and_revision_are_required(self):
        for overrides in ({"target_document": None}, {"workspace": "relative"},
                          {"expected_revision": None}, {"expected_revision": "obsolete"}):
            with self.subTest(overrides=overrides), self.assertRaises(GuardError):
                self.start(**overrides)
        self.assertIsNone(self.controller.task)

    def test_external_native_scalar_edit_stales_before_dispatch(self):
        self.start()
        self.observer.slotChangedObject(self.obj, "Length")
        with self.assertRaisesRegex(GuardError, "STALE_REVISION"):
            self.controller.check("Target", "Target")
        self.assertEqual("stale", self.controller.task.status)

    def test_read_only_recompute_does_not_stale(self):
        task = self.start()
        before = task.current_revision
        self.observer.slotBeforeRecomputeDocument(self.doc)
        self.observer.slotChangedObject(self.obj, "Shape")
        self.observer.slotRecomputedDocument(self.doc)
        self.controller.check("Target", "Target")
        self.assertEqual(before, self.controller.revision.token)

    def test_real_external_shape_assignment_is_not_ignored_after_recompute(self):
        self.start()
        self.observer.slotChangedObject(self.obj, "Shape")
        self.observer.slotBeforeRecomputeDocument(self.doc)
        self.observer.slotChangedObject(self.obj, "Shape")
        self.observer.slotRecomputedDocument(self.doc)
        with self.assertRaises(GuardError):
            self.controller.check("Target", "Target")

    def test_definition_edit_during_external_recompute_is_meaningful(self):
        self.start()
        self.observer.slotBeforeRecomputeDocument(self.doc)
        self.observer.slotChangedObject(self.obj, "ExpressionEngine")
        self.observer.slotRecomputedDocument(self.doc)
        with self.assertRaises(GuardError):
            self.controller.check("Target", "Target")

    def test_unrelated_document_is_not_in_guard_scope(self):
        self.start()
        other = SimpleNamespace(Name="Unrelated")
        obj = SimpleNamespace(Name="LargeUnrelatedObject", Document=other)
        self.observer.slotChangedObject(obj, "Shape")
        self.observer.slotDeletedDocument(other)
        self.controller.check("Target", "Target")

    def test_owned_native_edits_keep_epoch_and_exception_clears_ownership(self):
        task = self.start()
        with self.assertRaises(ValueError):
            with self.controller.revision.owned():
                self.observer.slotCreatedObject(self.obj)
                self.observer.slotChangedObject(self.obj, "Length")
                raise ValueError("tool failed")
        self.assertEqual(0, self.controller.revision.owned_depth)
        self.assertEqual(task.current_revision, self.controller.revision.token)
        self.observer.slotChangedObject(self.obj, "Length")
        with self.assertRaises(GuardError):
            self.controller.check("Target", "Target")

    def test_app_gui_mismatch_stops_before_any_tool(self):
        self.start()
        with self.assertRaisesRegex(GuardError, "TARGET_SWITCHED"):
            self.controller.check("Target", "Other")

    def test_closing_and_reopening_same_name_is_stale(self):
        self.start()
        self.observer.slotDeletedDocument(self.doc)
        with self.assertRaisesRegex(GuardError, "STALE_REVISION"):
            self.controller.check("Target", "Target")
        with self.assertRaises(GuardError):
            self.controller.reconcile("Target", "Target")

    def test_cancel_blocks_queued_writes_until_explicit_inspection_resume(self):
        task = self.start()
        generation = task.generation
        self.controller.stop()
        for _ in range(3):
            with self.assertRaisesRegex(GuardError, "queued tool rejected"):
                self.controller.check("Target", "Target", generation)
        with self.assertRaises(GuardError):
            self.controller.resume("Target", "Target")
        with self.assertRaises(GuardError):
            self.controller.reconcile("Target", "Target", busy=True)
        self.controller.reconcile("Target", "Target")
        self.controller.resume("Target", "Target")
        with self.assertRaisesRegex(GuardError, "Obsolete worker"):
            self.controller.check("Target", "Target", generation)
        self.controller.check("Target", "Target", task.generation)

    def test_edit_after_reconciliation_prevents_resume(self):
        self.start()
        self.controller.stop()
        self.controller.reconcile("Target", "Target")
        self.observer.slotChangedObject(self.obj, "Placement")
        with self.assertRaises(GuardError):
            self.controller.resume("Target", "Target")

    def test_return_is_not_success_and_failure_is_retained(self):
        task = self.start()
        self.controller.worker_returned()
        self.assertEqual("needs inspection", task.status)
        with self.assertRaises(GuardError):
            self.start()
        self.controller.fail("Native operation failed")
        self.controller.worker_returned()
        self.assertEqual("failed", task.status)
        self.assertEqual("Native operation failed", task.error)

    def test_whole_build_check_repair_check_saved_finite_phases(self):
        task = self.start()
        with self.assertRaises(GuardError):
            self.controller.advance("Saved", {"checks_passed": True})
        with self.assertRaises(GuardError):
            self.controller.advance("Check", {"representative_passed": True})
        self.controller.advance("Check", {"build_complete": True})
        self.controller.advance("Repair", {"failures": ["dimension outside criterion"]})
        with self.assertRaises(GuardError):
            self.controller.advance("Saved", {"checks_passed": True})
        self.controller.advance("Check", {"repair_complete": True})
        output = self.workspace / "fixture.FCStd"
        output.write_bytes(b"native fixture placeholder for controller test")
        self.controller.advance("Saved", {"checks_passed": True, "files": [str(output)]})
        self.controller.worker_returned()
        self.assertEqual("Saved", task.phase)
        self.assertEqual("saved", task.status)
        self.assertTrue(json.dumps(self.controller.snapshot()))

    def test_saved_files_must_exist_in_workspace_and_not_be_sources(self):
        source = self.workspace / "source.FCStd"
        source.write_bytes(b"source")
        self.start(source_files=[str(source)])
        self.controller.advance("Check", {"build_complete": True})
        for files in ([str(self.workspace / "missing.FCStd")], [str(source)]):
            with self.subTest(files=files), self.assertRaises((GuardError, FileNotFoundError)):
                self.controller.advance("Saved", {"checks_passed": True, "files": files})

    def test_source_change_stales_and_cannot_silently_resume(self):
        source = self.workspace / "drawing.txt"
        source.write_text("governing revision A")
        self.start(source_files=[str(source)])
        source.write_text("governing revision B with changed content")
        with self.assertRaisesRegex(GuardError, "SOURCE_CHANGED"):
            self.controller.check("Target", "Target")
        with self.assertRaisesRegex(GuardError, "Source contract changed"):
            self.controller.reconcile("Target", "Target")
        with self.assertRaises(GuardError):
            self.controller.resume("Target", "Target")
        # The inspected old task can be replaced with a new explicit contract.
        self.start(source_files=[str(source)], expected_revision=self.controller.revision.token)

    def test_status_has_no_native_object_dependency(self):
        self.start()
        for _ in range(1000):
            value = self.controller.snapshot()
        self.assertEqual("Target", value["target"])
        self.assertNotIn("geometry", value)

    def test_save_receipt_survives_recompute_but_rejects_owned_edit_after_recompute(self):
        self.start()
        output = self.workspace / 'saved.FCStd'
        output.write_bytes(b'native persisted content')
        self.controller.record_save(output)
        self.observer.slotBeforeRecomputeDocument(self.doc)
        self.observer.slotChangedObject(self.obj, 'Shape')
        self.observer.slotRecomputedDocument(self.doc)
        self.controller.check_save(output, saved=True, touched=False, pending_transaction=False)
        with self.controller.revision.owned():
            self.observer.slotChangedObject(self.obj, 'Length')
            self.observer.slotBeforeRecomputeDocument(self.doc)
            self.observer.slotChangedObject(self.obj, 'Shape')
            self.observer.slotRecomputedDocument(self.doc)
        # Native isSaved may stay true after recomputed edits; receipt must not.
        with self.assertRaisesRegex(GuardError, 'changed after save'):
            self.controller.check_save(output, saved=True, touched=False, pending_transaction=False)

    def test_save_receipt_requires_untouched_closed_transaction_and_unchanged_file(self):
        self.start()
        output = self.workspace / 'saved.FCStd'
        output.write_bytes(b'native persisted content')
        self.controller.record_save(output)
        for flags in ({'saved': False, 'touched': False, 'pending_transaction': False},
                      {'saved': True, 'touched': True, 'pending_transaction': False},
                      {'saved': True, 'touched': False, 'pending_transaction': True}):
            with self.subTest(flags=flags), self.assertRaises(GuardError):
                self.controller.check_save(output, **flags)
        output.write_bytes(b'externally replaced native content')
        with self.assertRaisesRegex(GuardError, 'changed on disk'):
            self.controller.check_save(output, saved=True, touched=False, pending_transaction=False)


if __name__ == "__main__":
    unittest.main()
