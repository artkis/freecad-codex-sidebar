from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'freecad_codex'))
from task_controller import TaskController, GuardError
from tool_policy import permitted_tools, validate_arguments


class ToolPolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        controller = TaskController()
        controller.bind('Target')
        self.task = controller.start({'target_document': 'Target', 'workspace': str(self.root),
            'prompt': 'Fixture', 'expected_revision': controller.revision.token}, 'model', 'low')

    def test_arbitrary_execution_and_custom_tools_are_opt_in(self):
        tools = [SimpleNamespace(name=name, main_thread=True) for name in
                 ('create_body', 'execute_code', 'run_macro', 'run_skill_script', 'user_test', 'capture_viewport', 'undo', 'redo')]
        self.assertEqual({'create_body'}, permitted_tools(tools))
        self.assertEqual({'execute_code', 'create_body'}, permitted_tools(tools, ['execute_code', 'create_body']))
        with self.assertRaisesRegex(GuardError, 'opt-in'):
            validate_arguments(self.task, 'execute_code', {'code': 'pass'}, lambda ref: True)
        self.task.allowed_tools = ['execute_code']
        self.assertEqual({'code': 'pass'}, validate_arguments(self.task, 'execute_code', {'code': 'pass'}, lambda ref: True))

    def test_protected_source_and_checkpoint_paths_rejected(self):
        source = self.root / 'source.FCStd'
        source.write_bytes(b'preserve')
        self.task.source_files[str(source.resolve())] = {}
        checkpoint = self.root / 'checkpoint.FCStd'
        self.task.checkpoint = str(checkpoint)
        for path in (source, checkpoint):
            with self.subTest(path=path), self.assertRaises(GuardError):
                validate_arguments(self.task, 'export_model', {'filename': str(path)}, lambda ref: True)

    def test_path_traversal_rejected_and_owned_relative_output_accepted(self):
        with self.assertRaisesRegex(GuardError, 'outside'):
            validate_arguments(self.task, 'export_model', {'filename': '../outside.step'}, lambda ref: True)
        result = validate_arguments(self.task, 'export_model', {'filename': 'owned.step'}, lambda ref: True)
        self.assertEqual(str(self.root / 'owned.step'), result['filename'])

    def test_foreign_document_and_object_are_rejected(self):
        for args in ({'document_name': 'Other'}, {'object_name': 'ForeignBox'}, {'objects': ['Owned', 'ForeignBox']}):
            with self.subTest(args=args), self.assertRaises(GuardError):
                validate_arguments(self.task, 'tool', args, lambda ref: ref == 'Owned')

    def test_task_budget_bounds_and_persisted_values(self):
        for value in (0, 501, True, 1.5):
            controller = TaskController()
            controller.bind('Target')
            with self.subTest(value=value), self.assertRaises(GuardError):
                controller.start({'target_document': 'Target', 'workspace': str(self.root),
                    'prompt': 'Fixture', 'expected_revision': controller.revision.token,
                    'max_tool_turns': value}, 'model', 'low')
        self.assertEqual(100, self.task.max_tool_turns)


if __name__ == '__main__':
    unittest.main()
