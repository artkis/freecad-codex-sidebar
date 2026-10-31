"""Small, FreeCAD-independent task state and target change guard.

The native adapter feeds document observer events here. No geometry is read by
status(), and a worker return is never treated as checked/saved success.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
import hashlib
import math
import time
import uuid


class GuardError(RuntimeError):
    pass


@dataclass
class TargetRevision:
    name: str
    identity: str = field(default_factory=lambda: uuid.uuid4().hex)
    epoch: int = 0
    closed: bool = False
    reason: str = ""
    owned_depth: int = 0
    recompute_depth: int = 0
    content_epoch: int = 0

    @property
    def token(self):
        return f"{self.identity}:{self.epoch}"

    def changed(self, reason, *, derived=False, metadata=False):
        # Only outputs emitted *inside* a signalled recompute are neutral.
        # A direct external Shape assignment outside that scope is an edit.
        if derived and self.recompute_depth:
            return
        self.content_epoch += 1
        if self.owned_depth or metadata:
            return
        self.epoch += 1
        self.reason = reason

    @contextmanager
    def owned(self):
        self.owned_depth += 1
        try:
            yield
        finally:
            self.owned_depth -= 1


@dataclass
class Task:
    id: str
    target_document: str
    source_revision: str
    current_revision: str
    workspace: str
    source_files: dict
    prompt: str
    model: str
    effort: str
    title: str = "CAD task"
    phase: str = "Build"
    status: str = "running"
    cancel_requested: bool = False
    reconciled: bool = False
    generation: int = 1
    started_at: float = field(default_factory=time.time)
    max_seconds: float = 1200
    last_tool: str = ""
    error: str = ""
    evidence: list = field(default_factory=list)
    allowed_tools: list = field(default_factory=list)
    reference_summary: str = ""
    acceptance_criteria: list = field(default_factory=list)
    mode: str = "act"
    source_contract_changed: bool = False
    checkpoint: str = ""
    max_tool_turns: int = 100
    system_prompt: str = ""
    save_receipt: dict = field(default_factory=dict)
    tool_profile: str = "inspect"
    required_tools: list | None = None
    tool_contract: dict = field(default_factory=dict)


def file_stamp(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise GuardError("Source must be an existing file")
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class TaskController:
    def __init__(self):
        self.revision = None
        self.task = None

    def bind(self, name):
        if self.task and self.task.status == "running":
            raise GuardError("Stop and reconcile before binding another document")
        self.revision = TargetRevision(name)
        return self.revision.token

    def start(self, payload, model, effort):
        if self.task and (self.task.status == "running" or
                          (self.task.status != "saved" and not self.task.reconciled)):
            raise GuardError("Previous task requires explicit inspection/reconciliation")
        if not self.revision or self.revision.closed:
            raise GuardError("Bind an open target document first")
        if payload.get("target_document") != self.revision.name:
            raise GuardError("An explicit bound target_document is required")
        if payload.get("expected_revision") != self.revision.token:
            raise GuardError("STALE_REVISION: inspect the bound target before submitting")
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise GuardError("A task prompt is required")
        workspace = Path(payload.get("workspace", "")).expanduser()
        if not payload.get("workspace") or not workspace.is_absolute() or not workspace.is_dir():
            raise GuardError("An existing absolute output workspace is required")
        workspace = workspace.resolve()
        sources = {str(Path(p).expanduser().resolve(strict=True)): file_stamp(p)
                   for p in payload.get("source_files", [])}
        timeout = float(payload.get("max_seconds", 1200))
        if not math.isfinite(timeout) or timeout <= 0:
            raise GuardError("max_seconds must be positive")
        max_turns = payload.get('max_tool_turns', 100)
        if isinstance(max_turns, bool) or not isinstance(max_turns, int) or not 1 <= max_turns <= 500:
            raise GuardError('max_tool_turns must be an integer from 1 to 500')
        # Import lazily to keep the policy/controller dependency acyclic.
        try:
            from .tool_profiles import TOOL_PROFILES, tool_names
        except ImportError:
            from tool_profiles import TOOL_PROFILES, tool_names
        tool_profile = payload.get('tool_profile', 'inspect')
        if not isinstance(tool_profile, str) or tool_profile not in TOOL_PROFILES:
            raise GuardError('Unknown CAD tool profile')
        allowed = tool_names(payload.get('allowed_tools', []), 'allowed_tools')
        required = payload.get('required_tools')
        required = None if required is None else tool_names(required, 'required_tools')
        mode = payload.get('mode', 'act')
        if mode not in ('act', 'plan'):
            raise GuardError('Unknown task mode')
        if mode == 'act' and tool_profile == 'custom' and not required:
            raise GuardError('Custom profile requires nonempty required_tools')
        self.task = Task(uuid.uuid4().hex, self.revision.name,
                         self.revision.token, self.revision.token,
                         str(workspace), sources, prompt, model, effort,
                         title=str(payload.get("title", "CAD task")), max_seconds=timeout)
        self.task.tool_profile = tool_profile
        self.task.allowed_tools = allowed
        self.task.required_tools = required
        self.task.reference_summary = str(payload.get("reference_summary", ""))[:6000]
        self.task.acceptance_criteria = list(payload.get("acceptance_criteria", []))
        self.task.mode = mode
        self.task.max_tool_turns = max_turns
        return self.task

    def check(self, app_target, gui_target, generation=None):
        t, r = self.task, self.revision
        if not t or t.status != "running" or t.cancel_requested:
            raise GuardError("Task is stopped or not running; queued tool rejected")
        if generation is not None and generation != t.generation:
            raise GuardError("Obsolete worker generation; queued tool rejected")
        if r.closed or r.name != t.target_document or r.token != t.current_revision:
            self.fail("STALE_REVISION: " + (r.reason or "bound target changed"), "stale")
            raise GuardError(t.error)
        if app_target != t.target_document or gui_target != t.target_document:
            self.fail("TARGET_SWITCHED: App and GUI must both show the bound target", "stale")
            raise GuardError(t.error)
        for path, original in t.source_files.items():
            try:
                stat = Path(path).stat()
                if (stat.st_size, stat.st_mtime_ns) == (original["size"], original["mtime_ns"]):
                    continue
                current = file_stamp(path)
                if current["sha256"] != original["sha256"]:
                    raise GuardError("SOURCE_CHANGED: inspect and re-approve source contract")
                t.source_files[path] = current
            except (OSError, GuardError) as exc:
                self.fail(str(exc), "stale")
                raise GuardError(t.error) from exc

    def stop(self):
        if self.task:
            self.task.cancel_requested = True
            self.task.reconciled = False
            if self.task.status not in ("failed", "stale"):
                self.task.status = "stopped"
            self.task.generation += 1

    def fail(self, error, status="failed"):
        if self.task:
            self.task.error = str(error)
            self.task.status = status
            self.task.cancel_requested = True
            self.task.reconciled = False
            self.task.generation += 1

    def worker_returned(self, error=None):
        if error:
            self.fail(error)
        elif self.task and self.task.status == "running":
            self.task.status = "needs inspection"
            self.task.reconciled = False

    def reconcile(self, app_target, gui_target, busy=False):
        if busy:
            raise GuardError("Execution is still active; wait before inspection")
        if not self.revision or self.revision.closed:
            raise GuardError("Target was closed; bind and inspect a new target")
        if app_target != self.revision.name or gui_target != self.revision.name:
            raise GuardError("Inspect/reconcile requires the bound target in App and GUI")
        if self.task:
            self.task.current_revision = self.revision.token
            self.task.reconciled = True
            if self.task.status != "saved":
                self.task.status = "reconciled"
            # Source contract changes require a new submission, never a silent resume.
            for path, original in self.task.source_files.items():
                if file_stamp(path)["sha256"] != original["sha256"]:
                    self.task.source_contract_changed = True
                    raise GuardError("Source contract changed; start a new task after reviewing sources")

    def resume(self, app_target, gui_target):
        t = self.task
        if not t or not t.reconciled or t.status == "saved" or t.source_contract_changed:
            raise GuardError("Inspect/reconcile the retained task before Resume")
        if not self.revision or self.revision.closed or self.revision.token != t.current_revision:
            raise GuardError("Target changed after inspection; inspect again")
        if app_target != t.target_document or gui_target != t.target_document:
            raise GuardError("App and GUI must both show the bound target")
        t.cancel_requested = False
        t.reconciled = False
        t.status = "running"
        t.error = ""
        t.generation += 1
        self.check(app_target, gui_target)

    def advance(self, phase, evidence):
        t = self.task
        if not t or t.status != "running":
            raise GuardError("No running task")
        allowed = {"Build": {"Check"}, "Check": {"Repair", "Saved"},
                   "Repair": {"Check"}, "Saved": set()}
        if phase not in allowed[t.phase]:
            raise GuardError(f"Invalid phase transition: {t.phase} -> {phase}")
        if not isinstance(evidence, dict) or not evidence:
            raise GuardError("Phase transition requires explicit evidence")
        if phase == "Check" and t.phase == "Build" and evidence.get("build_complete") is not True:
            raise GuardError("Build the entire authorized scope before Check")
        if phase == "Repair" and not evidence.get("failures"):
            raise GuardError("Repair requires a consolidated failure list")
        if phase == "Saved":
            if evidence.get("checks_passed") is not True or not evidence.get("files"):
                raise GuardError("Saved requires passed checks and verified output files")
            for output in evidence["files"]:
                path = Path(output).resolve(strict=True)
                if not path.is_file() or not path.is_relative_to(Path(t.workspace)):
                    raise GuardError("Saved output must exist inside the task workspace")
                if str(path) in t.source_files:
                    raise GuardError("Source files are protected from overwrite")
            t.status = "saved"
        t.phase = phase
        t.evidence.append({"phase": phase, "at": time.time(), "evidence": evidence})

    def record_save(self, filename):
        """Record synchronous native save completion, including owned edits."""
        t, r = self.task, self.revision
        path = Path(filename).resolve(strict=True)
        if not t or not r or not path.is_relative_to(Path(t.workspace)):
            raise GuardError('Native save receipt requires the bound workspace')
        t.save_receipt = {'filename': str(path), 'file': file_stamp(path),
                          'revision': r.token, 'content_epoch': r.content_epoch}

    def check_save(self, filename, *, saved, touched, pending_transaction):
        """Prove current native content matches the recorded save boundary.

        GUI Modified includes view/GUI state and is not native-content proof.
        The event epoch counts all owned definition edits as well as external
        edits; recomputing an edited part cannot restore an old receipt.
        """
        t, r = self.task, self.revision
        receipt = t.save_receipt if t else {}
        if not saved or touched or pending_transaction or not receipt:
            raise GuardError('Native target requires a completed commander_save, no touched content and no pending transaction')
        path = Path(filename).resolve(strict=True)
        if (str(path) != receipt['filename'] or not r or r.closed or
                r.token != receipt['revision'] or r.content_epoch != receipt['content_epoch']):
            raise GuardError('Native content changed after save; save the checked target again')
        if file_stamp(path)['sha256'] != receipt['file']['sha256']:
            raise GuardError('Saved native file changed on disk; inspect before accepting Saved')
        return dict(receipt)

    def snapshot(self):
        return {"task": asdict(self.task) if self.task else None,
                "target": self.revision.name if self.revision else None,
                "revision": self.revision.token if self.revision else None}

