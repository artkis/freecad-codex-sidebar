"""Exercise the actual Qt dispatch adapter with native/Qt stand-ins."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

MODULE_DIR = Path(__file__).resolve().parents[1] / "src" / "freecad_codex"
sys.path.insert(0, str(MODULE_DIR))
from provider_proxy import ProviderProxy
from test_backend import FakeClient


class FakeQObject:
    def __init__(self, parent=None):
        self._sender = None

    def sender(self):
        return self._sender


class Worker:
    def __init__(self, generation):
        self._commander_generation = generation
        self._commander_tools = {"native_tool"}
        self.results = []
        self.interrupted = False

    def set_tool_result(self, result):
        self.results.append(result)

    def requestInterruption(self):
        self.interrupted = True

    def isRunning(self):
        return False


class SupervisorGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        app = ModuleType("FreeCAD")
        gui = ModuleType("FreeCADGui")
        qt = ModuleType("PySide")
        thread = object()
        qt.QtCore = SimpleNamespace(QObject=FakeQObject,
            Slot=lambda *args: lambda function: function,
            QThread=SimpleNamespace(currentThread=lambda: thread))
        qt.QtGui = SimpleNamespace()
        qt.QtWidgets = SimpleNamespace()
        proxy = ModuleType("provider_proxy")
        proxy.default_token_path = lambda: Path(self.temp.name) / "provider.token"
        app.ActiveDocument = SimpleNamespace(Name="Target")
        self.native_doc = SimpleNamespace(Name='Target', Objects=[], FileName='',
                                         getObject=lambda name: SimpleNamespace(Name=name, Document=app.ActiveDocument)
                                         if name == 'OwnedBox' else None)
        app.getDocument = lambda name: self.native_doc if name == 'Target' else None
        gui.activeDocument = lambda: SimpleNamespace(Document=app.ActiveDocument)
        gui.getMainWindow = lambda: SimpleNamespace(thread=lambda: thread)
        modules = {"FreeCAD": app, "FreeCADGui": gui, "PySide": qt, "provider_proxy": proxy}
        self.patcher = patch.dict(sys.modules, modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        spec = importlib.util.spec_from_file_location("supervisor_under_test", MODULE_DIR / "arthur_supervisor.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        ctl = self.module._controller
        ctl.bind("Target")
        ctl.start({"target_document": "Target", "workspace": self.temp.name,
                   "expected_revision": ctl.revision.token, "prompt": "Native fixture"}, "model", "low")
        # This fixture isolates native dispatch; worker-creation tests below
        # exercise production contract construction with a synthetic registry.
        ctl.task.tool_contract = {'settings': {'profile': ctl.task.tool_profile,
            'required_tools': ctl.task.required_tools, 'allowed_tools': ctl.task.allowed_tools,
            'mode': ctl.task.mode}, 'names': ['native_tool', 'export_model', 'create_sketch'],
                                 'permissions': ['native_tool', 'export_model', 'create_sketch']}
        self.worker = Worker(ctl.task.generation)
        self.executions = []
        self.tool_result = SimpleNamespace(success=True, output="executed", error="")
        registry = SimpleNamespace(execute=self.execute_native)
        self.module._dock = SimpleNamespace(_worker=self.worker, _tool_registry=registry)
        self.module._persist = lambda: None
        self.module._request_provider_cancel = lambda: None
        self.gate = self.module.ToolGate()
        self.gate._sender = self.worker

    def execute_native(self, name, arguments):
        self.executions.append((name, arguments))
        return self.tool_result

    def test_success_executes_exactly_once_on_current_worker(self):
        self.gate.execute("native_tool", '{"length":12}')
        self.assertEqual([("native_tool", {"length": 12})], self.executions)
        self.assertTrue(self.worker.results[-1]["success"])
        self.assertEqual("running", self.module._controller.task.status)

    def test_cancel_prevents_an_already_queued_native_call(self):
        self.module._controller.stop()
        self.gate.execute("native_tool", '{}')
        self.assertFalse(self.executions)
        self.assertFalse(self.worker.results[-1]["success"])
        self.assertTrue(self.worker.interrupted)

    def test_obsolete_sender_is_replied_to_without_touching_new_task(self):
        old = Worker(0)
        self.gate._sender = old
        self.gate.execute("native_tool", '{}')
        self.assertFalse(self.executions)
        self.assertIn("Obsolete worker", old.results[-1]["error"])
        self.assertEqual("running", self.module._controller.task.status)

    def test_native_failure_stops_and_preserves_failure(self):
        self.tool_result = SimpleNamespace(success=False, output="partial", error="Invalid geometry")
        self.gate.execute("native_tool", '{}')
        self.assertEqual("failed", self.module._controller.task.status)
        self.assertEqual("Invalid geometry", self.module._controller.task.error)
        self.assertTrue(self.worker.interrupted)
        self.gate.execute("native_tool", '{}')
        self.assertEqual(1, len(self.executions))

    def test_target_switch_prevents_native_execution(self):
        self.module._active_names = lambda: ("Target", "Other")
        self.gate.execute("native_tool", '{}')
        self.assertFalse(self.executions)
        self.assertIn("TARGET_SWITCHED", self.worker.results[-1]["error"])

    def test_default_status_never_reads_native_documents(self):
        self.module._dock.isVisible = lambda: True
        self.module.App.listDocuments = lambda: self.fail("status enumerated documents")
        self.module._active_names = lambda: self.fail("status inspected native context")
        state = self.module.status()
        self.assertEqual("Target", state["target"])
        self.assertFalse(state["busy"])

    def test_outside_workspace_export_rejected_before_handler(self):
        self.worker._commander_tools.add('export_model')
        outside = str(Path(self.temp.name).parent / 'outside.step')
        self.gate.execute('export_model', json.dumps({'filename': outside, 'format': 'step'}))
        self.assertFalse(self.executions)
        self.assertIn('outside the authorized workspace', self.worker.results[-1]['error'])

    def test_foreign_object_export_rejected_before_handler(self):
        self.worker._commander_tools.add('export_model')
        self.gate.execute('export_model', '{"filename":"output.step","objects":["ForeignBox"]}')
        self.assertFalse(self.executions)
        self.assertIn('bound target', self.worker.results[-1]['error'])

    def test_native_object_export_normalizes_workspace_path_before_handler(self):
        self.worker._commander_tools.add('export_model')
        self.gate.execute('export_model', '{"filename":"output.step","objects":["OwnedBox"]}')
        self.assertEqual(str((Path(self.temp.name) / 'output.step').resolve()), self.executions[-1][1]['filename'])

    def test_resume_reuses_real_provider_thread_after_native_count_and_phase_change(self):
        module = self.module
        task = module._controller.task
        task.tool_contract = {}
        task.mode = 'plan'  # No native registry or inference in this regression.
        task.model = 'test-model'
        task.effort = 'medium'
        conversation = SimpleNamespace(clear_document_context=lambda: None, window_start=lambda: 0)
        module._dock.conversation = conversation
        prompts = []
        module._start_scoped_worker = lambda conversation, prompt, **kwargs: prompts.append(prompt)
        module._continue_send_compact()
        proxy = ProviderProxy(client_factory=FakeClient, cwd=self.temp.name, turn_timeout=10)
        self.addCleanup(proxy.close)
        proxy.start()
        body = {'model': 'test-model', 'reasoning_effort': 'medium', 'tools': [],
                'messages': [{'role': 'system', 'content': prompts[-1]},
                             {'role': 'user', 'content': 'Build fixture'}]}
        proxy.prepare(body)
        original_thread = proxy.thread_id
        proxy.cancel()
        proxy.notification({'method': 'turn/completed', 'params': {'threadId': proxy.thread_id,
                            'turn': {'id': proxy.turn_id, 'status': 'interrupted'}}})
        module._controller.stop()
        module._controller.reconcile('Target', 'Target')
        module._controller.resume('Target', 'Target')
        task.phase = 'Check'
        self.native_doc.Objects.append(SimpleNamespace(Name='NewBox', TypeId='Part::Box'))
        module._continue_send_compact()
        self.assertEqual(prompts[0], prompts[1])
        proxy.resume(True)
        body['messages'][0]['content'] = prompts[-1]
        body['messages'].append({'role': 'user', 'content': 'State inspected: phase Check; object_count 1. Continue.'})
        proxy.prepare(body)
        self.assertEqual(original_thread, proxy.thread_id)
        self.assertEqual(1, sum(method == 'thread/start' for method, _ in proxy.client.requests))

    def test_selection_reference_requires_same_revision_before_submit(self):
        revision = self.module._controller.revision
        refs = [{'document': 'Target', 'name': 'OwnedBox', 'sub': 'Face1',
                 'revision': revision.token, 'text': 'Target.OwnedBox.Face1'}]
        self.module._dock._attachment_strip = SimpleNamespace(get_references=lambda: refs)
        self.assertEqual('Target.OwnedBox.Face1', self.module._attached_reference_summary('Target'))
        revision.changed('OwnedBox.Shape')
        with self.assertRaisesRegex(RuntimeError, 'reattach'):
            self.module._attached_reference_summary('Target')

    def test_implicit_foreign_sketch_selection_rejects_before_handler(self):
        self.worker._commander_tools.add('create_sketch')
        # Same internal object name in different documents is still foreign.
        selected = SimpleNamespace(DocumentName='Other', ObjectName='OwnedBox', SubElementNames=['Face1'])
        self.module.Gui.Selection = SimpleNamespace(getSelectionEx=lambda: [selected])
        self.gate.execute('create_sketch', '{"plane":"XY"}')
        self.assertFalse(self.executions)
        self.assertIn('another document', self.worker.results[-1]['error'])

    def test_saved_accepts_native_receipt_despite_gui_only_dirty_flag(self):
        # Replays the observed native state: isSaved true, untouched, no
        # transaction, persisted output, but GUI Modified remains true.
        result_module = ModuleType('freecad_ai.tools.registry')
        result_module.ToolResult = lambda success, output, data=None, error='': SimpleNamespace(
            success=success, output=output, data=data or {}, error=error)
        with patch.dict(sys.modules, {'freecad_ai.tools.registry': result_module}):
            output = Path(self.temp.name) / 'saved.FCStd'
            output.write_bytes(b'saved native content')
            self.native_doc.FileName = str(output)
            self.native_doc.isSaved = lambda: True
            self.native_doc.isTouched = lambda: False
            self.native_doc.HasPendingTransaction = False
            self.module.Gui.getDocument = lambda name: SimpleNamespace(Modified=True)
            self.module._controller.task.phase = 'Check'
            self.module._controller.record_save(output)
            result = self.module._phase_tool('Saved', {'checks_passed': True, 'files': [str(output)]})
        self.assertTrue(result.success, result.error)
        self.assertEqual('saved', self.module._controller.task.status)
        evidence = self.module._controller.task.evidence[-1]['evidence']
        self.assertTrue(evidence['gui_modified'])
        self.assertIn('sha256', evidence['native_save_receipt']['file'])

    def test_configure_binds_profile_thinking_instead_of_inheriting_global(self):
        config_module = ModuleType('freecad_ai.config')
        cfg = SimpleNamespace(profiles={}, thinking='high')
        config_module.get_config = lambda: cfg
        config_module.ProviderConfig = lambda **kwargs: SimpleNamespace(**kwargs)
        config_module.save_config = lambda value: None
        with patch.dict(sys.modules, {'freecad_ai.config': config_module}):
            self.module.configure('Economical')
        self.assertEqual('high', cfg.thinking)
        self.assertEqual('low', cfg.profiles['Codex Economical'].thinking)
        self.assertEqual('low', cfg.profiles['Codex Economical'].params['reasoning_effort'])

    def scoped_worker(self):
        from test_tool_profiles import synthetic_registry, SyntheticTool
        module = self.module
        task = module._controller.task
        task.tool_contract = {}
        task.tool_profile = 'solid'
        task.required_tools = ['create_body', 'measure']
        registry = synthetic_registry()
        registry_module = ModuleType('freecad_ai.tools.registry')
        registry_module.ToolDefinition = SyntheticTool
        registry_module.ToolParam = lambda name, type, description, **kw: dict(name=name, type=type)
        patcher = patch.dict(sys.modules, {'freecad_ai.tools.registry': registry_module})
        patcher.start()
        self.addCleanup(patcher.stop)
        captured = []
        def start(conversation, prompt, **kwargs):
            captured.append(kwargs['registry'].to_openai_schema())
            worker = Worker(task.generation)
            worker.error_occurred = SimpleNamespace(connect=lambda callback: None)
            module._dock._worker = worker
        module._gate = self.gate
        module._originals['_start_worker'] = start
        module._start_scoped_worker(None, 'Synthetic contract', registry=registry)
        self.worker = module._dock._worker
        self.gate._sender = self.worker
        return registry, captured

    def test_advertised_tools_do_not_grant_execution_permission(self):
        self.scoped_worker()
        # Simulate a buggy/hostile caller advertising arbitrary execution.
        self.worker._commander_tools = {'execute_code'}
        self.gate.execute('execute_code', '{"code":"pass"}')
        self.assertFalse(self.executions)
        self.assertIn('frozen task permissions', self.worker.results[-1]['error'])

    def test_permitted_but_unselected_tool_is_rejected_at_native_gate(self):
        self.scoped_worker()
        task = self.module._controller.task
        self.assertIn('pad_sketch', task.tool_contract['permissions'])
        self.assertNotIn('pad_sketch', task.tool_contract['names'])
        self.worker._commander_tools = {'pad_sketch'}
        self.gate.execute('pad_sketch', '{}')
        self.assertIn('frozen task permissions', self.worker.results[-1]['error'])

    def test_act_resume_preserves_schema_and_real_provider_thread(self):
        from test_tool_profiles import SyntheticTool
        module = self.module
        registry, captured = self.scoped_worker()
        proxy = ProviderProxy(client_factory=FakeClient, cwd=self.temp.name, turn_timeout=10)
        self.addCleanup(proxy.close)
        proxy.start()
        body = {'model': 'test-model', 'reasoning_effort': 'medium', 'tools': captured[-1],
                'messages': [{'role': 'system', 'content': 'Synthetic contract'},
                             {'role': 'user', 'content': 'Create and measure synthetic body'}]}
        proxy.prepare(body)
        original_thread = proxy.thread_id
        proxy.cancel()
        proxy.notification({'method': 'turn/completed', 'params': {'threadId': proxy.thread_id,
                            'turn': {'id': proxy.turn_id, 'status': 'interrupted'}}})
        module._controller.stop()
        module._controller.reconcile('Target', 'Target')
        module._controller.resume('Target', 'Target')
        registry.register(SyntheticTool('new_unrelated_operation'))
        registry.tools = dict(reversed(list(registry.tools.items())))
        module._start_scoped_worker(None, 'Synthetic contract', registry=registry)
        self.assertEqual(captured[0], captured[1])
        proxy.resume(True)
        body['tools'] = captured[-1]
        body['messages'].append({'role': 'user', 'content': 'Target inspected. Continue.'})
        proxy.prepare(body)
        self.assertEqual(original_thread, proxy.thread_id)
        self.assertEqual(1, sum(method == 'thread/start' for method, _ in proxy.client.requests))

    def test_schema_drift_blocks_resume_before_provider_or_worker(self):
        module = self.module
        registry, captured = self.scoped_worker()
        module._controller.stop()
        module._controller.reconcile('Target', 'Target')
        registry.get('measure').description = 'Changed native contract'
        module._native_registry = lambda task: registry
        module.provider = lambda *args: self.fail('Provider resumed before schema validation')
        with self.assertRaisesRegex(RuntimeError, 'schema changed'):
            module.resume()
        self.assertEqual(1, len(captured))
        self.assertEqual('reconciled', module._controller.task.status)

    def test_current_permissions_are_checked_independently_of_worker_advertisement(self):
        self.scoped_worker()
        self.module._controller.task.allowed_tools = ['measure']
        self.gate.execute('create_body', '{}')
        self.assertIn('scope changed', self.worker.results[-1]['error'])

    def test_plan_start_freezes_empty_tools_and_cannot_resume_as_act(self):
        module = self.module
        task = module._controller.task
        task.tool_contract = {}
        task.mode = 'plan'
        module._gate = self.gate
        self.worker.error_occurred = SimpleNamespace(connect=lambda callback: None)
        module._originals['_start_worker'] = lambda *args, **kw: None
        module._start_scoped_worker(None, 'Plan fixture', registry=None)
        self.assertEqual([], task.tool_contract['names'])
        self.assertEqual(set(), self.worker._commander_tools)
        module._controller.stop()
        module._controller.reconcile('Target', 'Target')
        task.mode = 'act'
        module.provider = lambda *args: self.fail('Provider must not resume changed mode')
        with self.assertRaisesRegex(RuntimeError, 'scope changed'):
            module.resume()


if __name__ == "__main__":
    unittest.main()

