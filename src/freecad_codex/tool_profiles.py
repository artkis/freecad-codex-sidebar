"""Task tool advertisements, separate from the enforced permission policy.

Explicit profiles describe CAD operations, never model/effort or prompt keywords.
The retained contract pins names AND schemas. Dependency drift fails closed;
new upstream tools cannot silently join an existing task.
"""
from copy import copy, deepcopy
import hashlib
import json

try:
    from .task_controller import GuardError
    from .tool_policy import permitted_tools
except ImportError:
    from task_controller import GuardError
    from tool_policy import permitted_tools

INSPECT = frozenset({'get_document_state', 'describe_model', 'measure',
                     'list_faces', 'list_edges', 'read_variable_set', 'read_spreadsheet'})
SKETCH = INSPECT | {'create_body', 'create_sketch', 'edit_sketch'}
SOLID = SKETCH | {'pad_sketch', 'pocket_sketch', 'revolve_sketch', 'loft_sketches',
                 'sweep_sketch', 'fillet_edges', 'chamfer_edges', 'create_primitive',
                 'boolean_operation', 'transform_object', 'duplicate_object',
                 'delete_object', 'linear_pattern', 'polar_pattern'}
TOOL_PROFILES = {
    'inspect': INSPECT,
    'sketch': SKETCH,
    'solid': SOLID,
    'parameters': INSPECT | {'create_variable_set', 'create_spreadsheet',
                            'edit_variable_set', 'edit_spreadsheet', 'set_expression'},
    'export': INSPECT | {'export_model'},
    'custom': frozenset(),
}
CONTROL_TOOLS = frozenset({'commander_phase', 'commander_save'})


def tool_names(value, field):
    if not isinstance(value, (list, tuple)) or any(not isinstance(n, str) or not n for n in value):
        raise GuardError(field + ' must be a list of nonempty tool names')
    return sorted(set(value))


def select_tools(tools, profile, required_tools=None, explicit=()):
    """Select a bounded advertisement only within independent permissions.

    required_tools narrows a named profile; custom requires an exact list.
    Missing requested tools are errors, never a fallback to all tools.
    """
    if not isinstance(profile, str) or profile not in TOOL_PROFILES:
        raise GuardError('Unknown CAD tool profile: ' + str(profile))
    explicit = tool_names(explicit, 'allowed_tools')
    requested = (set(tool_names(required_tools, 'required_tools'))
                 if required_tools is not None else set(TOOL_PROFILES[profile]))
    if profile == 'custom' and not requested:
        raise GuardError('Custom profile requires nonempty required_tools')
    if profile != 'custom' and not requested <= TOOL_PROFILES[profile]:
        raise GuardError('Required tools are outside the selected CAD profile')
    available = {t.name for t in tools}
    missing = requested - available
    if missing:
        raise GuardError('Required tools are unavailable: ' + ', '.join(sorted(missing)))
    permitted = permitted_tools(tools, explicit)
    denied = requested - permitted
    if denied:
        raise GuardError('Required tools are not authorized: ' + ', '.join(sorted(denied)))
    return sorted(requested), sorted(permitted)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _settings(task):
    return {'profile': task.tool_profile, 'required_tools': task.required_tools,
            'allowed_tools': task.allowed_tools, 'mode': task.mode}


def check_scope(task):
    if task.tool_contract and _settings(task) != task.tool_contract['settings']:
        raise GuardError('Tool scope changed; start a new task instead of Resume')


def freeze_plan(task):
    check_scope(task)
    if not task.tool_contract:
        task.tool_contract = deepcopy({'settings': _settings(task), 'names': [],
            'permissions': [], 'schema_json': '[]',
            'schema_sha256': hashlib.sha256(b'[]').hexdigest()})


class ScopedRegistry:
    """Owned definitions + immutable serialized advertisement for one worker.

    The supervisor remains the execution/argument/target gate. This wrapper
    does not turn arbitrary Python into a sandbox.
    """
    def __init__(self, registry, task):
        contract = task.tool_contract
        settings = _settings(task)
        check_scope(task)
        tools = registry.list_tools()
        if contract:
            names = contract['names']
            permitted = permitted_tools(tools, task.allowed_tools) | CONTROL_TOOLS
            if not set(names) <= permitted:
                raise GuardError('Retained tools are no longer authorized or available')
            permissions = contract['permissions']
        else:
            names, permissions = select_tools(tools, task.tool_profile,
                                              task.required_tools, task.allowed_tools)
            controls = {'commander_phase'}
            if task.tool_profile != 'inspect':
                controls.add('commander_save')
            names = sorted(set(names) | controls)
            permissions = sorted(set(permissions) | controls)
        # Copy schema metadata, not native handlers or their bound documents.
        self._registry = type(registry)()
        for name in names:
            source = registry.get(name)
            if source is None or not source.main_thread:
                raise GuardError('Retained tool unavailable on GUI thread: ' + name)
            tool = copy(source)
            tool.parameters = deepcopy(source.resolve_params())
            tool.lazy_params = None
            self._registry.register(tool)
        self._schema = deepcopy(self._registry.to_openai_schema())
        serialized = _canonical(self._schema)
        if contract and serialized != contract['schema_json']:
            raise GuardError('Tool schema changed; start a new task instead of Resume')
        if not contract:
            task.tool_contract = deepcopy({
                'settings': settings, 'names': names, 'permissions': permissions,
                'schema_json': serialized,
                'schema_sha256': hashlib.sha256(serialized.encode()).hexdigest(),
            })

    def get(self, name):
        return self._registry.get(name)

    def execute(self, name, arguments):
        return self._registry.execute(name, arguments)

    def to_openai_schema(self, filter_names=None):
        # Never let a later filter silently change this conversation's schema.
        return deepcopy(self._schema)

    def to_anthropic_schema(self, filter_names=None):
        return [{'name': t['function']['name'], 'description': t['function']['description'],
                 'input_schema': deepcopy(t['function']['parameters'])} for t in self._schema]
