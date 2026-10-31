"""Synthetic registry fixtures; no FreeCAD geometry, model calls or accounts."""
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'freecad_codex'))
from task_controller import TaskController, GuardError
from tool_profiles import TOOL_PROFILES, CONTROL_TOOLS, ScopedRegistry, select_tools


@dataclass
class SyntheticTool:
    name: str
    description: str = 'Synthetic CAD operation'
    parameters: dict = field(default_factory=lambda: {'type': 'object', 'properties': {}})
    handler: object = None
    main_thread: bool = True
    lazy_params: object = None

    def resolve_params(self):
        return self.parameters


class SyntheticRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool

    def get(self, name):
        return self.tools.get(name)

    def list_tools(self):
        return list(self.tools.values())

    def to_openai_schema(self):
        return [{'type': 'function', 'function': {'name': t.name,
                'description': t.description, 'parameters': (deepcopy(t.parameters)
                if isinstance(t.parameters, dict) else {'type': 'object', 'properties': {
                    p['name']: {'type': p['type']} for p in t.parameters}})}}
                for t in self.tools.values()]

    def execute(self, name, arguments):
        return self.tools[name].handler(**arguments)


def synthetic_registry(names=None):
    registry = SyntheticRegistry()
    if names is None:
        names = sorted(set().union(*TOOL_PROFILES.values(), CONTROL_TOOLS,
                                 {'execute_code', 'capture_viewport', 'future_tool'}))
    for name in names:
        registry.register(SyntheticTool(name))
    return registry


class ToolProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = TaskController()
        self.controller.bind('SyntheticTarget')
        self.registry = synthetic_registry()

    def start(self, **kwargs):
        return self.controller.start(dict(target_document='SyntheticTarget',
            expected_revision=self.controller.revision.token, workspace=self.temp.name,
            prompt='Synthetic fixture', **kwargs), 'model', 'low')

    def test_named_profiles_select_only_their_operations(self):
        for profile, expected in TOOL_PROFILES.items():
            if profile == 'custom':
                continue
            with self.subTest(profile=profile):
                names, permissions = select_tools(self.registry.list_tools(), profile)
                self.assertEqual(set(names), expected)
                self.assertNotIn('future_tool', names)
                self.assertIn('future_tool', permissions)  # Separate concepts.
                self.assertNotIn('execute_code', permissions)

    def test_explicit_requirements_narrow_a_profile(self):
        names, _ = select_tools(self.registry.list_tools(), 'solid',
                                ['create_body', 'create_sketch', 'pad_sketch', 'measure'])
        self.assertEqual(names, ['create_body', 'create_sketch', 'measure', 'pad_sketch'])
        with self.assertRaisesRegex(GuardError, 'outside'):
            select_tools(self.registry.list_tools(), 'inspect', ['pad_sketch'])

    def test_unknown_profiles_and_unavailable_requirements_fail_closed(self):
        with self.assertRaisesRegex(GuardError, 'Unknown'):
            select_tools(self.registry.list_tools(), 'solidd')
        with self.assertRaisesRegex(GuardError, 'unavailable'):
            select_tools(self.registry.list_tools(), 'custom', ['invented_tool'])
        self.registry.tools.pop('measure')
        with self.assertRaisesRegex(GuardError, 'unavailable'):
            select_tools(self.registry.list_tools(), 'inspect')

    def test_custom_selection_cannot_authorize_arbitrary_code(self):
        with self.assertRaisesRegex(GuardError, 'not authorized'):
            select_tools(self.registry.list_tools(), 'custom', ['execute_code'])
        names, _ = select_tools(self.registry.list_tools(), 'custom',
                                ['execute_code'], ['execute_code'])
        self.assertEqual(['execute_code'], names)
        self.registry.get('execute_code').main_thread = False
        with self.assertRaisesRegex(GuardError, 'not authorized'):
            select_tools(self.registry.list_tools(), 'custom', ['execute_code'], ['execute_code'])

    def test_explicit_permission_list_is_not_expanded_by_profile(self):
        with self.assertRaisesRegex(GuardError, 'not authorized'):
            select_tools(self.registry.list_tools(), 'solid', ['pad_sketch'], ['measure'])

    def test_contract_is_detached_from_caller_and_registry_mutations(self):
        required = ['measure']
        task = self.start(tool_profile='custom', required_tools=required)
        scoped = ScopedRegistry(self.registry, task)
        original = scoped.to_openai_schema()
        required.append('execute_code')
        self.registry.get('measure').parameters['properties']['new'] = {'type': 'string'}
        emitted = scoped.to_openai_schema()
        emitted[0]['function']['description'] = 'changed by caller'
        self.assertEqual(original, scoped.to_openai_schema())
        self.assertEqual(original, scoped.to_openai_schema({'execute_code'}))
        self.assertIsNone(scoped.get('execute_code'))
        self.assertEqual(['measure'], task.required_tools)

    def test_resume_keeps_schema_and_order_despite_registry_growth(self):
        task = self.start(tool_profile='solid', required_tools=['measure', 'create_body'])
        first = ScopedRegistry(self.registry, task).to_openai_schema()
        self.controller.stop()
        self.controller.reconcile('SyntheticTarget', 'SyntheticTarget')
        self.controller.resume('SyntheticTarget', 'SyntheticTarget')
        rebuilt = synthetic_registry(reversed(list(self.registry.tools) + ['another_new_tool']))
        self.assertEqual(first, ScopedRegistry(rebuilt, task).to_openai_schema())

    def test_resume_rejects_changed_selected_schema(self):
        task = self.start(required_tools=['measure'])
        ScopedRegistry(self.registry, task)
        self.registry.get('measure').parameters['properties']['required_length'] = {'type': 'number'}
        with self.assertRaisesRegex(GuardError, 'schema changed'):
            ScopedRegistry(self.registry, task)

    def test_resume_rejects_missing_or_background_selected_tool(self):
        task = self.start(required_tools=['measure'])
        ScopedRegistry(self.registry, task)
        self.registry.get('measure').main_thread = False
        with self.assertRaisesRegex(GuardError, 'no longer authorized'):
            ScopedRegistry(self.registry, task)
        self.registry.tools.pop('measure')
        with self.assertRaisesRegex(GuardError, 'no longer authorized'):
            ScopedRegistry(self.registry, task)

    def test_resume_rejects_profile_permission_requirements_and_mode_changes(self):
        task = self.start(required_tools=['measure'])
        ScopedRegistry(self.registry, task)
        for field, value in [('tool_profile', 'solid'), ('allowed_tools', ['measure']),
                             ('required_tools', ['pad_sketch']), ('mode', 'plan')]:
            old = getattr(task, field)
            with self.subTest(field=field):
                setattr(task, field, value)
                with self.assertRaisesRegex(GuardError, 'scope changed'):
                    ScopedRegistry(self.registry, task)
                setattr(task, field, old)

    def test_default_inspection_has_no_save_or_modeling_tools(self):
        task = self.start()
        names = {t['function']['name'] for t in ScopedRegistry(self.registry, task).to_openai_schema()}
        self.assertEqual(set(TOOL_PROFILES['inspect']) | {'commander_phase'}, names)

    def test_invalid_selection_payload_never_creates_a_task(self):
        for payload in [{'tool_profile': 'unknown'}, {'tool_profile': []},
                        {'tool_profile': 'custom'}, {'required_tools': 'measure'},
                        {'allowed_tools': 'execute_code'}]:
            with self.subTest(payload=payload), self.assertRaises(GuardError):
                self.start(**payload)
            self.assertIsNone(self.controller.task)
