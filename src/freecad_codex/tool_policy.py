"""Pre-dispatch bounds for structured native tools, not a Python sandbox."""
from pathlib import Path
try:
    from .task_controller import GuardError
except ImportError:
    from task_controller import GuardError

# Tools that execute arbitrary code, load user logic, change document scope or
# capture images require exact per-task opt-in. They are never default tools.
OPT_IN = frozenset({'execute_code', 'execute_code_headless', 'run_macro',
                    'run_skill_script', 'use_skill', 'switch_document',
                    'capture_viewport', 'select_geometry', 'list_documents', 'undo', 'redo'})
PATH_KEYS = frozenset({'filename', 'filepath', 'file_path', 'output_path', 'output_file', 'path'})
OBJECT_KEYS = frozenset({'object_name', 'body_name', 'sketch_name', 'feature_name',
    'object1', 'object2', 'target', 'target2', 'tool_object', 'assembly_name',
    'part_name', 'part1_name', 'part2_name', 'support', 'profile_name', 'path_name'})
OBJECT_ARRAY_KEYS = frozenset({'objects', 'sketch_names', 'feature_names', 'part_names'})


def permitted_tools(tools, explicit=()):
    explicit = set(explicit)
    safe = {t.name for t in tools if t.main_thread and t.name != 'optimize_iteration'
            and ((t.name not in OPT_IN and not t.name.startswith('user_')) or t.name in explicit)}
    return (explicit & safe) if explicit else safe


def output_path(task, value):
    if not isinstance(value, str) or not value.strip():
        raise GuardError('A nonempty output path is required')
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(task.workspace) / path
    path = path.resolve()
    if not path.is_relative_to(Path(task.workspace).resolve()):
        raise GuardError('Output path is outside the authorized workspace')
    if str(path) in task.source_files:
        raise GuardError('Protected source files cannot be overwritten')
    if task.checkpoint and path == Path(task.checkpoint).resolve():
        raise GuardError('The initial checkpoint is protected from overwrite')
    if not path.parent.is_dir() or path.is_dir():
        raise GuardError('Output parent must exist and output must name a file')
    return str(path)


def validate_arguments(task, name, arguments, resolve_object):
    """Validate explicit paths/refs before any registered handler is invoked.

    Arbitrary code remains an explicit trusted capability; its contents cannot
    be constrained by this structured-argument policy.
    """
    if not isinstance(arguments, dict):
        raise GuardError('Tool arguments must be an object')
    if name in OPT_IN or name.startswith('user_'):
        if name not in task.allowed_tools:
            raise GuardError('This tool requires explicit per-task opt-in')
    normalized = dict(arguments)
    for key, value in arguments.items():
        if key in {'document', 'document_name', 'doc_name', 'target_document'} and value:
            if value != task.target_document:
                raise GuardError('Tool document differs from the bound target')
        if key in PATH_KEYS:
            normalized[key] = output_path(task, value)
        if key in OBJECT_KEYS and value:
            if not isinstance(value, str):
                raise GuardError('Object references must use internal names')
            if not resolve_object(value):
                raise GuardError('Object reference is absent from the bound target: ' + value)
        if key in OBJECT_ARRAY_KEYS and value:
            if not isinstance(value, list) or any(not isinstance(v, str) or not resolve_object(v) for v in value):
                raise GuardError('All object references must belong to the bound target')
    return normalized


def validate_selection(task, name, selections):
    # create_sketch can infer attachment from GUI selection even when its
    # structured arguments omit support. Upstream drops the document identity
    # during that inference, so reject foreign/unknown selections first.
    if name != 'create_sketch':
        return
    for selected in selections:
        document = getattr(selected, 'DocumentName', None)
        if not document:
            obj = getattr(selected, 'Object', None)
            doc = getattr(obj, 'Document', None)
            document = getattr(doc, 'Name', None)
        if document != task.target_document:
            raise GuardError('Implicit sketch selection belongs to another document; select the bound target explicitly')
