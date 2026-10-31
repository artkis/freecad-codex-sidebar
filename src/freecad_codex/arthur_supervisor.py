"""Portable companion for the unmodified ghbalf/freecad-ai Qt dock.

Native access stays on the GUI thread. The HTTP poller never calls FreeCAD RPC.
The guard enforces cooperative single-writer ownership, not a Python sandbox.
"""
import json
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui
try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui
try:
    from .task_controller import TaskController, GuardError
    from .document_guard import DocumentObserver
    from .tool_policy import validate_arguments, validate_selection, output_path
    from .tool_profiles import TOOL_PROFILES, ScopedRegistry, check_scope, freeze_plan
    from .provider_proxy import default_token_path
except ImportError:
    from task_controller import TaskController, GuardError
    from document_guard import DocumentObserver
    from tool_policy import validate_arguments, validate_selection, output_path
    from tool_profiles import TOOL_PROFILES, ScopedRegistry, check_scope, freeze_plan
    from provider_proxy import default_token_path

ROOT = Path(__file__).resolve().parent
STATE = default_token_path().parent
PROFILES = {'Economical': ('gpt-6-luna', 'low'),
            'Standard': ('gpt-6.1-sol', 'medium'),
            'Complex': ('gpt-6-astra', 'high')}
_controller = TaskController()
_observer = None
_dock = _gate = _timer = _label = _choice = _target = _workspace = _sources = None
_box = None
_tool_choice = None
_proxy = {}
_process = None
_tool_inflight = False
_started = False
_stop_poll = threading.Event()
_originals = {}
_last_worker = None
_poll_thread = None
_cancel_lock = threading.Lock()


def provider(path='/status', body=None, timeout=3):
    token = default_token_path().read_text(encoding='ascii').strip()
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request('http://127.0.0.1:9876' + path, data=data,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _active_names():
    native = App.ActiveDocument
    visible = Gui.activeDocument()
    gui_doc = visible.Document if visible else None
    return native.Name if native else None, gui_doc.Name if gui_doc else None


def _busy():
    if not _dock:
        return False
    workers = [getattr(_dock, '_worker', None)] + list(getattr(_dock, '_detached_workers', []))
    return _tool_inflight or any(w and w.isRunning() for w in workers)


def _require_idle():
    if _busy() or _proxy.get('active'):
        raise GuardError('One writer is active; wait for its stopped state')


def _native_modified(doc):
    visible = Gui.getDocument(doc.Name)
    if visible is None or not hasattr(visible, 'Modified'):
        raise GuardError('Native GUI modified-state readback is unavailable')
    return bool(visible.Modified)


def bind_target(name):
    """Explicit selection only; never infer a target from App.ActiveDocument."""
    _require_idle()
    doc = App.getDocument(name)
    if doc is None:
        raise GuardError('Target document is not open')
    revision = _controller.bind(name)
    App.setActiveDocument(name)
    if _active_names() != (name, name):
        raise GuardError('Target activation did not bind both App and GUI')
    return {'target_document': name, 'revision': revision}


def inspect(target_document=None, detailed=False):
    """Explicit target-only inventory; no shape traversal and no recompute."""
    _require_idle()
    name = target_document or (_controller.revision.name if _controller.revision else None)
    if not name:
        raise GuardError('Select an explicit target_document')
    if not _controller.revision or name != _controller.revision.name:
        bind_target(name)
    doc = App.getDocument(name)
    if doc is None or _controller.revision.closed:
        raise GuardError('Bound document is no longer open')
    app_name, gui_name = _active_names()
    value = {'target_document': name, 'active_document': app_name,
             'gui_document': gui_name, 'revision': _controller.revision.token,
             'file': doc.FileName, 'modified': _native_modified(doc),
             'object_count': len(doc.Objects), 'recomputed': False}
    if detailed:
        value['objects'] = [{'name': obj.Name, 'type': obj.TypeId,
                            'state': list(getattr(obj, 'State', []))} for obj in doc.Objects]
    return value


def status():
    # No document enumeration, property inspection or geometry on this path.
    return {'plugin': 'ghbalf/freecad-ai', 'supervisor': 'FreeCAD Codex Sidebar',
            'busy': _busy(), **_controller.snapshot(), 'provider': dict(_proxy),
            'panel_visible': bool(_dock and _dock.isVisible()),
            'single_writer_scope': 'sidebar worker; external clients must cooperate'}


def result():
    state = status()
    if _dock:
        worker = getattr(_dock, '_worker', None) or _last_worker
        state['text'] = getattr(worker, '_full_response', '')
        state['conversation_id'] = _dock.conversation.conversation_id
    return state


def _persist():
    if _controller.task:
        STATE.mkdir(parents=True, exist_ok=True)
        path = STATE / (_controller.task.id + '.json')
        temp = path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(result(), indent=2, default=str), encoding='utf-8')
        temp.replace(path)


class ToolGate(QtCore.QObject):
    @QtCore.Slot(str, str)
    def execute(self, name, arguments):
        global _tool_inflight
        worker = self.sender()
        if worker is None:
            return
        reply = {'success': False, 'output': '', 'error': ''}
        try:
            if worker is not getattr(_dock, '_worker', None):
                raise GuardError('Obsolete worker; queued tool rejected')
            if QtCore.QThread.currentThread() != Gui.getMainWindow().thread():
                raise GuardError('GUI thread dispatch mismatch')
            _controller.check(*_active_names(), generation=getattr(worker, '_commander_generation', -1))
            if _controller.revision.recompute_depth:
                raise GuardError('Incomplete native recompute notification; inspect before retry')
            check_scope(_controller.task)
            contract = _controller.task.tool_contract
            if (not contract or name not in contract['permissions']
                    or name not in contract['names']):
                raise GuardError('Tool outside the frozen task permissions or profile')
            if name not in getattr(worker, '_commander_tools', set()):
                raise GuardError('Tool outside the assigned task scope')
            _tool_inflight = True
            _controller.task.last_tool = name
            doc = App.getDocument(_controller.task.target_document)
            arguments = validate_arguments(_controller.task, name, json.loads(arguments),
                lambda ref: bool(doc and doc.getObject(ref)))
            if name == 'create_sketch':
                validate_selection(_controller.task, name, Gui.Selection.getSelectionEx())
            with _controller.revision.owned():
                tool_result = _dock._tool_registry.execute(name, arguments)
            reply = {'success': bool(tool_result.success), 'output': tool_result.output,
                     'error': tool_result.error}
            if _active_names() != (_controller.task.target_document,) * 2:
                raise GuardError('Tool changed target context; explicit inspection required')
            if not reply['success']:
                _controller.fail(reply['error'] or 'CAD tool failed; inspect actual state')
        except Exception as exc:
            reply = {'success': False, 'output': '', 'error': str(exc)}
            if worker is getattr(_dock, '_worker', None):
                _controller.fail(str(exc))
        finally:
            _tool_inflight = False
            worker.set_tool_result(reply)
            if _controller.task and _controller.task.cancel_requested:
                worker.requestInterruption()
                _request_provider_cancel()
            _persist()

    @QtCore.Slot(str)
    def error(self, message):
        if self.sender() is getattr(_dock, '_worker', None):
            _controller.fail(message)
            _request_provider_cancel()
            _persist()


def _phase_tool(phase, evidence):
    from freecad_ai.tools.registry import ToolResult
    try:
        if phase == 'Saved':
            doc = App.getDocument(_controller.task.target_document)
            if not doc.FileName:
                raise GuardError('Native target has no saved filename')
            receipt = _controller.check_save(doc.FileName, saved=bool(doc.isSaved()),
                touched=bool(doc.isTouched()), pending_transaction=bool(doc.HasPendingTransaction))
            files = evidence.get('files', [])
            if str(Path(doc.FileName).resolve()) not in [str(Path(p).resolve()) for p in files]:
                raise GuardError('Saved evidence must include the native target file')
            evidence = dict(evidence, native_save_receipt=receipt,
                            gui_modified=_native_modified(doc))
        _controller.advance(phase, evidence)
        return ToolResult(True, json.dumps({'phase': phase, 'status': _controller.task.status}))
    except Exception as exc:
        return ToolResult(False, '', error=str(exc))


def _save_tool(filename):
    from freecad_ai.tools.registry import ToolResult
    try:
        task = _controller.task
        path = Path(output_path(task, filename))
        if path.suffix.lower() != '.fcstd':
            raise GuardError('Native save requires a .FCStd filename')
        if path.exists():
            raise GuardError('Choose a new native output filename; existing files are preserved')
        doc = App.getDocument(task.target_document)
        doc.saveAs(str(path))
        if not path.is_file() or not path.stat().st_size:
            raise GuardError('Native save did not produce an output file')
        _controller.record_save(str(path))
        return ToolResult(True, 'Native target saved to ' + str(path), data={'filename': str(path)})
    except Exception as exc:
        return ToolResult(False, '', error=str(exc))


def _native_registry(task):
    check_scope(task)
    if task.mode == 'plan':
        return None
    from freecad_ai.tools.registry import ToolRegistry
    from freecad_ai.tools.freecad_tools import ALL_TOOLS
    from freecad_ai.tools.parameter_tools import PARAMETER_TOOLS
    registry = ToolRegistry()
    for tool in list(ALL_TOOLS) + list(PARAMETER_TOOLS):
        registry.register(tool)
    if any(name.startswith('user_') for name in task.allowed_tools):
        from freecad_ai.tools.setup import create_default_registry
        explicit_registry = create_default_registry(include_mcp=False)
        for name in task.allowed_tools:
            tool = explicit_registry.get(name)
            if tool:
                registry.register(tool)
    return registry


def _prepare_registry(registry):
    task = _controller.task
    check_scope(task)
    if task.mode == 'plan':
        if registry is not None:
            raise GuardError('Plan mode cannot receive a native tool registry')
        freeze_plan(task)
        return None
    from freecad_ai.tools.registry import ToolDefinition, ToolParam
    registry.register(ToolDefinition('commander_phase',
        'Record Build -> Check -> Repair -> Check -> Saved with evidence. Check requires whole build_complete=true; '
        'Repair requires failures; Saved requires checks_passed=true and existing workspace files.',
        [ToolParam('phase', 'string', 'Next phase', enum=['Check', 'Repair', 'Saved']),
         ToolParam('evidence', 'object', 'Measured checks, failure list, or verified saved file paths')], _phase_tool))
    registry.register(ToolDefinition('commander_save',
        'Save the bound native target as a new .FCStd file inside the authorized workspace; sources/checkpoint are protected.',
        [ToolParam('filename', 'string', 'New native output filename or workspace path')], _save_tool))
    return ScopedRegistry(registry, _controller.task)


def _start_scoped_worker(conversation, system_prompt, **kwargs):
    global _last_worker
    registry = kwargs.get('registry')
    if not registry and _controller.task.mode != 'plan':
        raise GuardError('Native tool registry is required')
    registry = _prepare_registry(registry)
    if registry is None:
        _originals['_start_worker'](conversation, system_prompt, **kwargs)
        _last_worker = _dock._worker
        _last_worker._commander_generation = _controller.task.generation
        _last_worker._commander_tools = set()
        _last_worker.error_occurred.connect(_gate.error)
        return
    kwargs['registry'] = registry
    _dock._tool_registry = registry
    kwargs['filter_names'] = frozenset(_controller.task.tool_contract['names'])
    _originals['_start_worker'](conversation, system_prompt, **kwargs)
    worker = _dock._worker
    worker._commander_generation = _controller.task.generation
    worker._commander_tools = kwargs['filter_names']
    worker.error_occurred.connect(_gate.error)
    _last_worker = worker


def _continue_send_compact():
    """Use supported registry APIs without upstream all-document prompt dumps.

    The selected source summary and task contract replace unrelated project
    context. No auto-MCP connection, image capture, or vision fallback.
    """
    task = _controller.task
    _controller.check(*_active_names())
    registry = _native_registry(task)
    _dock._tool_registry = registry
    doc = App.getDocument(task.target_document)
    objects = doc.Objects
    packet = {'target_document': task.target_document, 'phase': task.phase,
              'workspace': task.workspace, 'protected_sources': task.source_files,
              'reference_summary': task.reference_summary,
              'acceptance_criteria': task.acceptance_criteria,
              'tool_profile': task.tool_profile, 'required_tools': task.required_tools,
              'object_count': len(objects),
              'object_index_first_40': [{'name': o.Name, 'type': o.TypeId} for o in objects[:40]]}
    system_prompt = task.system_prompt or (
        'You are an expert FreeCAD native CAD assistant. Use the exact bound document. '
        'Use structured tools and native editable features; preserve parametric history. '
        'Sketch/pad/pocket operations belong in an explicit PartDesign Body; use named objects '
        'and select tool-supported attachment instead of guessing face orientation. '
        'Measure against governing source and explicit acceptance criteria. Do not swallow errors. '
        'No automatic screenshots, full-document geometry dumps, unrelated memory or project context. '
        'Inspect only the needed native inputs once. Build the whole authorized scope first, '
        'then consolidated Check, Repair, Check, Saved. Record phases with commander_phase. '
        'Never overwrite protected source files. Use commander_save for native output inside the workspace. Stop requires '
        'explicit inspection before retry; no rollback is implied. A returned reply is not a passed check. '
        'No raw code-block auto execution. '
        + ('Plan only; do not execute changes. ' if task.mode == 'plan' else '')
        + '\nBound task packet:\n' + json.dumps(packet, default=str))
    task.system_prompt = system_prompt  # Immutable for this retained provider thread.
    conversation = _dock.conversation
    conversation.clear_document_context()
    _start_scoped_worker(conversation, system_prompt, registry=registry,
        filter_names=None, describe_fn=None, strip_images=True,
        start_index=conversation.window_start(), needs_vision=False)


def configure(profile='Standard'):
    from freecad_ai.config import get_config, ProviderConfig, save_config
    cfg = get_config()
    for label, (model, effort) in PROFILES.items():
        cfg.profiles['Codex ' + label] = ProviderConfig(name='custom',
            base_url='http://127.0.0.1:9876/v1', api_key='file:' + str(default_token_path()),
            model=model, thinking=effort, params={'reasoning_effort': effort}, vision_override=False, tools_detected=True)
    cfg.active_profile = 'Codex ' + profile
    cfg.keep_dock_on_workbench_switch = True
    cfg.max_tool_turns = _controller.task.max_tool_turns if _controller.task else 100
    cfg.max_retries = 1
    cfg.log_token_usage = True
    cfg.fallback_profiles = []
    cfg.enable_tools = True
    cfg.viewport_capture = 'off'
    save_config(cfg)


def _ensure_provider(workspace):
    global _process, _proxy
    try:
        live = provider(timeout=1)
    except (OSError, ValueError):
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / 'provider.log').open('a', encoding='utf-8') as log:
            _process = subprocess.Popen([str(Path(App.ConfigGet('BinPath')) / 'python.exe'),
                str(ROOT / 'provider_proxy.py'), '--token-file', str(default_token_path()),
                '--workspace', workspace], cwd=str(ROOT), stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(20):
            try:
                live = provider(timeout=0.2)
                break
            except (OSError, ValueError):
                time.sleep(0.1)
        else:
            raise GuardError('Provider did not start; inspect its local log')
    if Path(live.get('workspace', '')).resolve() != Path(workspace).resolve():
        raise GuardError('Provider workspace differs; restart provider with the authorized workspace')
    _proxy = live


def submit(payload):
    if not _dock:
        raise GuardError('Start the sidebar first')
    _require_idle()
    profile = payload.get('profile', 'Standard')
    if profile not in PROFILES:
        raise GuardError('Unknown task profile')
    payload = dict(payload)
    doc = App.getDocument(payload.get('target_document', ''))
    sources = list(payload.get('source_files', []))
    if doc and doc.FileName and Path(doc.FileName).is_file() and doc.FileName not in sources:
        sources.append(doc.FileName)
    payload['source_files'] = sources
    task = _controller.start(payload, *PROFILES[profile])
    try:
        _controller.check(*_active_names())
        _prepare_registry(_native_registry(task))
        _ensure_provider(task.workspace)
        if payload.get('new_chat', True):
            provider('/reset', {'acknowledge_state_inspected': True})
            _dock._new_chat()
        elif _proxy.get('requires_state_inspection'):
            raise GuardError('Provider requires inspected Resume or explicit New chat')
        provider('/budget', {'task_id': task.id, 'max_seconds': task.max_seconds,
                             'max_tool_turns': task.max_tool_turns})
        configure(profile)
        _dock._capture_mode_override = 'off'
        if task.mode == 'act':
            doc = App.getDocument(task.target_document)
            save_copy = getattr(doc, 'saveCopy', None)
            if not callable(save_copy):
                raise GuardError('Native saveCopy is unavailable; create an explicit checkpoint before act mode')
            checkpoint = Path(task.workspace) / ('checkpoint-' + task.id + '.FCStd')
            with _controller.revision.owned():
                save_copy(str(checkpoint))
            if not checkpoint.is_file() or not checkpoint.stat().st_size:
                raise GuardError('Native checkpoint was not written; no model tool started')
            task.checkpoint = str(checkpoint)
        (STATE / 'sidebar.json').write_text(json.dumps({'workspace': task.workspace}), encoding='utf-8')
        _choice.setCurrentText(profile)
        _dock.mode_combo.setCurrentIndex(1 if payload.get('mode', 'act') == 'act' else 0)
        contract = ('You are the sole sidebar CAD writer. Bound target: ' + task.target_document +
            '. App and GUI must remain on this exact target. Output workspace: ' + task.workspace +
            '. Protected source files: ' + json.dumps(list(task.source_files)) + '. '
            'Build the complete authorized scope first, then consolidated Check, Repair, Check, Saved. '
            'Whole-build-first supersedes representative-only gates. Inspect relevant native inputs once; '
            'avoid repeated full geometry scans. Use native editable features. Never overwrite source files. '
            'Use commander_phase to record transitions and measured evidence. Save native output only inside '
            'the workspace. A tool failure or Stop requires inspection before retry; neither implies rollback. '
            'Do not invent fit, approval, fabrication or saved status. Report measured limits concisely.\n\n')
        _dock.input_edit.setPlainText(contract + task.prompt)
        _originals['_send_message']()
        _persist()
        return {'accepted': True, 'task_id': task.id, 'model': task.model, 'effort': task.effort,
                'tool_profile': task.tool_profile, 'tool_count': len(task.tool_contract['names'])}
    except Exception as exc:
        _controller.fail(str(exc))
        _persist()
        raise


def _request_provider_cancel():
    def stop_provider():
        if not _cancel_lock.acquire(blocking=False):
            return
        try:
            provider('/cancel', {}, timeout=10)
        except Exception:
            pass
        finally:
            _cancel_lock.release()
    threading.Thread(target=stop_provider, daemon=True).start()


def cancel():
    _controller.stop()
    if _dock and getattr(_dock, '_worker', None):
        _dock._worker.requestInterruption()
    _request_provider_cancel()
    _persist()
    return {'stop_requested': True, 'rollback': False, 'requires_reconciliation': True}


def reconcile():
    _require_idle()
    live = inspect(detailed=True)
    _controller.reconcile(*_active_names(), busy=False)
    _persist()
    return {'inspected': True, **live, 'resume_requires_explicit_action': True}


def resume():
    _require_idle()
    task = _controller.task
    if not task or not task.reconciled:
        raise GuardError('Inspect/reconcile before Resume')
    _prepare_registry(_native_registry(task))
    live = provider()
    if live.get('requires_state_inspection'):
        provider('/resume', {'acknowledge_state_inspected': True})
    _controller.resume(*_active_names())
    doc = App.getDocument(task.target_document)
    updated = {'phase': task.phase, 'revision': task.current_revision,
               'object_count': len(doc.Objects), 'last_error': task.error}
    _dock.input_edit.setPlainText('Continue retained task ' + task.id + ' at phase ' + task.phase +
        '. Current target was explicitly inspected; prior edits remain. Inspect failures before further edits. '
        'Keep the same target, protected sources and output workspace. ' + task.prompt +
        '\nCurrent inspected state: ' + json.dumps(updated))
    try:
        _originals['_send_message']()
    except Exception as exc:
        _controller.fail(str(exc))
        raise
    finally:
        _persist()
    return {'resumed': True, 'task_id': task.id, 'phase': task.phase}


def _panel_action(action):
    import html
    try:
        value = action()
        if value and value.get('inspected'):
            message = f"Inspected {value['target_document']}: {value['object_count']} objects. Existing edits remain; Resume is available."
        elif value and value.get('accepted'):
            message = f"Task started with {value['model']} / {value['effort']}; CAD tools: {value['tool_profile']} ({value['tool_count']})."
        elif value and value.get('resumed'):
            message = f"Resumed retained task at {value['phase']}."
        elif value and value.get('stop_requested'):
            message = 'Stop requested. Inspect / reconcile before Resume.'
        elif value and 'targets' in value:
            message = f"Refreshed {len(value['targets'])} open document targets."
        elif value and 'references' in value:
            message = f"Attached {value['references']} selection references."
        else:
            message = 'Action completed.'
        _dock._append_html('<p>' + html.escape(message) + '</p>')
        return value
    except Exception as exc:
        _dock._append_html('<p>' + html.escape(str(exc)) + '</p>')
        return {'success': False, 'error': str(exc)}


def _panel_send():
    if _busy():
        return cancel()
    prompt = _dock.input_edit.toPlainText().strip()
    if not prompt:
        return
    def send():
        name = _target.currentText()
        if not _controller.revision or name != _controller.revision.name:
            bind_target(name)
        references = _attached_reference_summary(name)
        return submit({'prompt': prompt, 'title': prompt[:70], 'profile': _choice.currentText(),
            'tool_profile': _tool_choice.currentText(),
            'target_document': name, 'expected_revision': inspect()['revision'],
            'workspace': _workspace.text().strip(),
            'source_files': [p.strip() for p in _sources.text().split(';') if p.strip()],
            'reference_summary': references,
            'mode': 'act' if _dock.mode_combo.currentIndex() else 'plan'})
    return _panel_action(send)


def _attached_reference_summary(name):
    refs = _dock._attachment_strip.get_references()
    if len(refs) > 20:
        raise GuardError('Keep selection attachments to 20 references or fewer')
    doc = App.getDocument(name)
    for ref in refs:
        if (ref.get('document') != name or not doc.getObject(ref.get('name', ''))
                or ref.get('revision') != _controller.revision.token):
            raise GuardError('Selection reference lacks the bound document identity; remove and reattach it')
    return '\n'.join(ref.get('text', '')[:500] for ref in refs)[:6000]


def _capture_selection_references():
    _require_idle()
    name = _target.currentText()
    if not _controller.revision or _controller.revision.name != name:
        bind_target(name)
    selections = Gui.Selection.getSelectionEx()
    refs = []
    from freecad_ai.core.references import describe_reference
    for selected in selections:
        obj = selected.Object
        if obj.Document.Name != name:
            raise GuardError('Select references from the bound target document')
        for sub in list(selected.SubElementNames) or [None]:
            refs.append({'document': name, 'revision': _controller.revision.token,
                'name': obj.Name, 'label': obj.Label, 'sub': sub,
                'text': name + '.' + obj.Name + ('.' + sub if sub else '') + ': ' + describe_reference(obj, sub)[:450]})
            if len(refs) > 20:
                raise GuardError('Select 20 references or fewer')
    if not refs:
        raise GuardError('Select a target object, face or edge first')
    for ref in refs:
        _dock._attachment_strip.add_reference(ref)
    return {'references': len(refs)}


def _panel_reference():
    return _panel_action(_capture_selection_references)


def _panel_reconcile():
    name = _target.currentText()
    if not _controller.revision or name != _controller.revision.name:
        bind_target(name)
    return reconcile()


def _refresh_targets():
    _require_idle()
    selected = _target.currentText()
    _target.clear()
    _target.addItems(sorted(App.listDocuments()))
    if selected:
        _target.setCurrentText(selected)
    else:
        _target.setCurrentIndex(-1)
    return {'targets': [_target.itemText(i) for i in range(_target.count())]}


def _choose_workspace():
    path = QtWidgets.QFileDialog.getExistingDirectory(_dock, 'Output workspace', _workspace.text())
    if path:
        _workspace.setText(path)


def _raw_code_disabled(blocks):
    _dock._append_html('<p>Use a structured CAD tool to execute changes.</p>')


def _refresh():
    if not _dock:
        return
    busy = _busy()
    task = _controller.task
    if task and task.status == 'running' and not busy and not _proxy.get('active'):
        _controller.worker_returned()
        _persist()
    if task and busy and time.time() - task.started_at > task.max_seconds and not task.cancel_requested:
        cancel()
    for widget in (_choice, _tool_choice, _target, _workspace, _sources):
        widget.setEnabled(not busy)
    model, effort = (task.model, task.effort) if task else PROFILES[_choice.currentText()]
    account = _proxy.get('account', {})
    usage = _proxy.get('usage_total') or _proxy.get('usage') or {}
    breakdown = _proxy.get('usage_breakdown') or {}
    text = f"Codex | {account.get('planType', 'connecting')} | {model} | {effort}\n"
    text += f'{task.title} | {task.phase} | {task.status}' if task else 'Select target and workspace, then enter a task'
    if task:
        text += f"\nCAD tools: {task.tool_profile} | {len(task.tool_contract.get('names', []))} selected"
    if usage:
        text += '\nTokens: ' + str(usage.get('totalTokens', 'unknown'))
    if breakdown:
        text += ' | Input: ' + str(breakdown.get('input_tokens', '?'))
        text += ' | Cached: ' + str(breakdown.get('cached_input_tokens', '?'))
        text += ' | Output: ' + str(breakdown.get('output_tokens', '?'))
    if task and task.error:
        text += '\n' + task.error[:240]
    _label.setText(text)
    _label.setToolTip(task.error if task else '')


def _poll_provider(stop_event):
    global _proxy
    while not stop_event.is_set():
        try:
            live = provider(timeout=2)
            if not stop_event.is_set():
                _proxy = live
        except Exception as exc:
            if not stop_event.is_set():
                _proxy = {'connection_error': str(exc)[:150]}
        stop_event.wait(2)


def start():
    global _dock, _gate, _started, _timer, _label, _choice, _target, _workspace, _sources, _observer, _box, _stop_poll, _poll_thread, _tool_choice
    if _started:
        _dock.show()
        return status()
    from freecad_ai.ui.chat_widget import get_chat_dock
    _dock = get_chat_dock()
    if _busy():
        raise GuardError('Existing sidebar worker is active; stop it before installing companion')
    _dock.show()
    _box = QtWidgets.QWidget(_dock)
    layout = QtWidgets.QVBoxLayout(_box)
    layout.setContentsMargins(6, 3, 6, 3)
    title = QtWidgets.QLabel('FreeCAD Codex')
    title.setStyleSheet('font-size: 14px; font-weight: 600;')
    layout.addWidget(title)
    _label = QtWidgets.QLabel('Select a target and private output workspace')
    _label.setWordWrap(True)
    layout.addWidget(_label)
    form = QtWidgets.QFormLayout()
    _target = QtWidgets.QComboBox()
    _target.addItems(sorted(App.listDocuments()))
    _target.setCurrentIndex(-1)
    form.addRow('Target document', _target)
    _workspace = QtWidgets.QLineEdit()
    _workspace.setPlaceholderText('Absolute existing output folder')
    try:
        saved = json.loads((STATE / 'sidebar.json').read_text(encoding='utf-8'))
        if Path(saved.get('workspace', '')).is_dir():
            _workspace.setText(saved['workspace'])
    except (OSError, ValueError, KeyError):
        pass
    workspace_row = QtWidgets.QHBoxLayout()
    workspace_row.addWidget(_workspace)
    browse = QtWidgets.QPushButton('Browse')
    browse.clicked.connect(_choose_workspace)
    workspace_row.addWidget(browse)
    form.addRow('Workspace', workspace_row)
    _sources = QtWidgets.QLineEdit()
    _sources.setPlaceholderText('Protected source files, separated by ; (optional)')
    form.addRow('Sources', _sources)
    _choice = QtWidgets.QComboBox()
    _choice.addItems(list(PROFILES))
    _choice.setCurrentText('Standard')
    form.addRow('Model / effort', _choice)
    _tool_choice = QtWidgets.QComboBox()
    _tool_choice.addItems([name for name in TOOL_PROFILES if name != 'custom'])
    _tool_choice.setToolTip('CAD operations for a new task. Resume keeps the original scope.')
    form.addRow('CAD tools', _tool_choice)
    layout.addLayout(form)
    row = QtWidgets.QHBoxLayout()
    for label, action in (('Refresh targets', _refresh_targets), ('Inspect / reconcile', _panel_reconcile),
                          ('Stop', cancel), ('Resume', resume)):
        button = QtWidgets.QPushButton(label)
        button.clicked.connect(lambda checked=False, action=action: _panel_action(action))
        row.addWidget(button)
    layout.addLayout(row)
    _dock.widget().layout().insertWidget(0, _box)
    _observer = DocumentObserver(_controller)
    App.addDocumentObserver(_observer)
    _gate = ToolGate(_dock)
    for name, replacement in (('_execute_tool_call', _gate.execute),
                              ('_start_worker', _start_scoped_worker),
                              ('_continue_send', _continue_send_compact),
                              ('_send_message', _panel_send),
                              ('_handle_act_mode', _raw_code_disabled)):
        _originals[name] = getattr(_dock, name)
        setattr(_dock, name, replacement)
    _dock.send_btn.clicked.disconnect()
    _dock.send_btn.clicked.connect(_panel_send)
    if getattr(_dock, '_reference_btn', None):
        _dock._reference_btn.clicked.disconnect()
        _dock._reference_btn.clicked.connect(_panel_reference)
    _timer = QtCore.QTimer(_dock)
    _timer.timeout.connect(_refresh)
    _timer.start(1000)
    _stop_poll = threading.Event()
    _poll_thread = threading.Thread(target=_poll_provider, args=(_stop_poll,), daemon=True)
    _poll_thread.start()
    _started = True
    return status()


def shutdown():
    """Idle-only hot-reload detach; never rolls back or kills the provider."""
    global _started, _dock
    _require_idle()
    if not _started:
        return {'detached': True}
    _timer.stop()
    _stop_poll.set()
    App.removeDocumentObserver(_observer)
    _dock.send_btn.clicked.disconnect(_panel_send)
    for name, original in _originals.items():
        setattr(_dock, name, original)
    _dock.send_btn.clicked.connect(_originals['_send_message'])
    if getattr(_dock, '_reference_btn', None):
        _dock._reference_btn.clicked.disconnect(_panel_reference)
        _dock._reference_btn.clicked.connect(_dock._add_selection_reference)
    _box.setParent(None)
    _box.deleteLater()
    _gate.deleteLater()
    _started = False
    _dock = None
    return {'detached': True, 'provider_still_running': bool(_process and _process.poll() is None)}

