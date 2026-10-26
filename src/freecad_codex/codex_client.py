"""Small stdio client for the installed Codex app-server.

Uses Codex's own ChatGPT sign-in; it never reads or copies auth.json.  This
module does not import FreeCAD/Qt and never runs a model turn on its own.
Callbacks run off the GUI thread and must marshal GUI work appropriately.
"""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable


DEFAULT_CWD = Path(__file__).resolve().parent


class CodexError(RuntimeError):
    pass


def _runtime_version(binary):
    """Bounded metadata probe; never starts app-server or a model turn."""
    try:
        result = subprocess.run([str(binary), "--version"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=2,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            return None
        match = re.search(r"\bcodex(?:-cli)?\s+(\d+)\.(\d+)\.(\d+)(?:-([\w.-]+))?",
                          result.stdout, re.IGNORECASE)
        if not match:
            return None
        major, minor, patch = (int(v) for v in match.group(1, 2, 3))
        suffix = match.group(4) or ""
        return (major, minor, patch, not bool(suffix),
                re.sub(r"[\d.]+", "", suffix), tuple(int(v) for v in re.findall(r"\d+", suffix)))
    except (OSError, subprocess.TimeoutExpired):
        return None


def discover_binary(explicit=None) -> Path:
    """Find a runtime using metadata only, without pinning a build hash.

    An explicit selection is authoritative: a bad override must not silently
    run a different installation. Prefer verified desktop app runtimes over
    possibly older npm/PATH installations. A desktop bin parent can be a
    launcher rather than the CLI, so versioned children take precedence.
    """
    override = explicit or os.environ.get("FREECAD_CODEX_BINARY") or os.environ.get("CODEX_BINARY")
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_file():
            raise CodexError("Configured Codex executable is missing: " + str(candidate))
        return candidate.resolve()
    candidates = []
    fallbacks = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        root = Path(local) / "OpenAI" / "Codex" / "bin"
        candidates.extend(root.glob("*/codex.exe"))
        fallbacks.extend(root.glob("codex.exe"))
    if sys.platform == "darwin":
        candidates.extend([Path("/Applications/Codex.app/Contents/Resources/codex"),
                           Path.home() / "Applications/Codex.app/Contents/Resources/codex"])
    available = sorted((p for p in candidates if p.is_file()),
                       key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)
    versions = [(version, p) for p in available[:8] if (version := _runtime_version(p)) is not None]
    if versions:
        return max(versions, key=lambda item: (item[0], str(item[1])))[1].resolve()
    located = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if located:
        fallbacks.append(Path(located))
    for candidate in fallbacks:
        if candidate.is_file() and _runtime_version(candidate) is not None:
            return candidate.resolve()
    raise CodexError("No usable Codex runtime found. Install Codex or set FREECAD_CODEX_BINARY to its executable")


class CodexClient:
    """Thread-safe request/reply transport with asynchronous tool delivery.

    on_notification(message): a full method/params notification dictionary.
    on_tool_call(params): return the documented DynamicToolCallResponse, or
        an ordinary JSON-serializable value to wrap as successful inputText.
    start(): auth/catalog metadata only; it does NOT create a conversation.
    """

    def __init__(self, on_notification: Callable | None = None,
                 on_tool_call: Callable | None = None, *, binary=None,
                 cwd=None, config_overrides: list[str] | None = None):
        self.on_notification = on_notification or (lambda message: None)
        self.on_tool_call = on_tool_call
        # Resolve at start so constructing a controller does not depend on an
        # installed runtime (and remains straightforward to test).
        self.binary = Path(binary).expanduser() if binary else None
        self.cwd = Path(cwd) if cwd else DEFAULT_CWD
        self.config_overrides = list(config_overrides or [])
        self.process: subprocess.Popen | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._counter = 0
        self._closed = threading.Event()
        self._stderr = deque(maxlen=60)
        self._threads: list[threading.Thread] = []
        self.metadata: dict[str, Any] = {}
        self.diagnostics: dict[str, Any] = {}

    def _emit(self, message: dict) -> None:
        try:
            self.on_notification(message)
        except Exception as exc:
            # A UI callback must not kill the protocol reader.
            self._stderr.append("notification callback: " + str(exc)[:400])

    @staticmethod
    def _mcp_names() -> list[str]:
        # Read settings, never credentials. Quoted table names are supported by
        # tomllib on FreeCAD's Python; the conservative fallback covers this PC.
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        path = home / "config.toml"
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8-sig")
        try:
            import tomllib
            return sorted(tomllib.loads(text).get("mcp_servers", {}))
        except ImportError:
            return sorted(set(re.findall(r"(?m)^\[mcp_servers\.([A-Za-z0-9_-]+)\]", text)))

    def _command(self) -> list[str]:
        # Verified against `codex features list` in 0.164.0-alpha.11.1. Only
        # this child process is narrowed. Parent/global settings stay intact.
        disabled = ["apps", "plugins", "browser_use", "computer_use",
                    "shell_tool", "unified_exec", "memories", "multi_agent"]
        args = [str(self.binary), "app-server", "--listen", "stdio://"]
        for feature in disabled:
            args.extend(["--disable", feature])
        args.extend(["--enable", "skip_host_skill_discovery"])
        # The host supplies the workbench system prompt and a bounded task
        # contract. Do not rediscover this PC's unrelated, large AGENTS.md chain
        # in every CAD worker turn. This is a child-process setting only.
        overrides = ["notify=[]", "web_search=\"disabled\"", "agents.enabled=false",
                     "project_doc_max_bytes=0"]
        names = self._mcp_names()
        for name in names:
            # Codex's -c dotted-key parser treats embedded quotes literally;
            # these installed server identifiers are plain TOML key names.
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise CodexError("Unsupported MCP identifier in scoped override: " + name)
            overrides.append("mcp_servers." + name + ".enabled=false")
        overrides += self.config_overrides
        for value in overrides:
            args.extend(["-c", value])
        self.diagnostics = {"binary": str(self.binary), "transport": "stdio",
                            "disabled_features": disabled,
                            "disabled_mcp_servers": names,
                            "host_skill_discovery": False,
                            "project_doc_max_bytes": 0,
                            "auth": "Codex managed; existing CODEX_HOME",
                            "hindsight_bank": os.environ.get("HINDSIGHT_BANK_ID")}
        return args

    def start(self, timeout: float = 45) -> dict:
        if timeout <= 0:
            raise ValueError("Startup timeout must be positive")
        if self.process is not None:
            if self.process.poll() is None and self.metadata:
                return self.metadata
            raise CodexError("Client cannot be reused after its process exits; create a new client")
        self.binary = discover_binary(self.binary)
        if not self.cwd.is_dir():
            raise CodexError("Configured Codex working directory is missing: " + str(self.cwd))
        env = os.environ.copy()
        self.process = subprocess.Popen(
            self._command(), cwd=str(self.cwd), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for name, target in (("codex-reader", self._read_loop),
                             ("codex-stderr", self._stderr_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()
        try:
            deadline = time.monotonic() + timeout

            def startup_request(method, params):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Codex metadata startup timed out")
                return self.request(method, params, timeout=remaining)

            init = startup_request("initialize", {
                "clientInfo": {"name": "arthur_freecad_sidebar",
                               "title": "Arthur FreeCAD Agent", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            self.notify("initialized", {})
            account = startup_request("account/read", {"refreshToken": False})
            catalog = startup_request("model/list", {"limit": 100, "includeHidden": False})
            models = list(catalog.get("data", []))
            cursor = catalog.get("nextCursor")
            seen_cursors = set()
            while cursor:
                if cursor in seen_cursors or len(seen_cursors) >= 20:
                    raise CodexError("Codex model catalog pagination did not terminate")
                seen_cursors.add(cursor)
                catalog = startup_request("model/list", {"limit": 100, "includeHidden": False,
                                                       "cursor": cursor})
                models.extend(catalog.get("data", []))
                cursor = catalog.get("nextCursor")
            raw_account = account.get("account") or {}
            safe_account = {key: raw_account.get(key) for key in ("type", "planType")
                            if key in raw_account}
            safe_account["signedIn"] = bool(raw_account)
            safe_account["requiresOpenaiAuth"] = account.get("requiresOpenaiAuth")
            self.metadata = {"account": safe_account, "models": models,
                             "server": init, "diagnostics": self.diagnostics}
            return self.metadata
        except Exception:
            self.close()
            raise

    def _send(self, payload: dict) -> None:
        with self._write_lock:
            if self._closed.is_set() or self.process is None or self.process.poll() is not None:
                raise CodexError("Codex app-server is not running")
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.process.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 45) -> Any:
        slot = {"event": threading.Event(), "reply": None}
        with self._pending_lock:
            self._counter += 1
            request_id = self._counter
            self._pending[request_id] = slot
        try:
            self._send({"id": request_id, "method": method, "params": params or {}})
            if not slot["event"].wait(timeout):
                raise TimeoutError("Codex request timed out: " + method +
                                   ". A timeout does not cancel an active turn.")
            reply = slot["reply"] or {}
            if "error" in reply:
                error = reply["error"]
                raise CodexError(str(error.get("message", error)) if isinstance(error, dict) else str(error))
            return reply.get("result")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def _read_loop(self) -> None:
        try:
            for line in self.process.stdout:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._stderr.append("Non-JSON stdout ignored: " + line[:250])
                    continue
                if "method" in message and "id" in message:
                    # The callback may call request(), so never execute it on
                    # the transport's sole reader thread.
                    threading.Thread(target=self._handle_server_request, args=(message,),
                                     name="codex-tool", daemon=True).start()
                elif "id" in message:
                    with self._pending_lock:
                        slot = self._pending.get(message["id"])
                        if slot:
                            slot["reply"] = message
                            slot["event"].set()
                else:
                    self._emit(message)
        except Exception as exc:
            self._stderr.append("reader: " + str(exc)[:400])
        finally:
            self._fail_pending("Codex app-server connection closed")
            if not self._closed.is_set():
                self._emit({"method": "client/disconnected", "params": {
                    "exitCode": self.process.poll(), "stderr": self.stderr_tail()}})

    def _handle_server_request(self, message: dict) -> None:
        request_id, method, params = message["id"], message["method"], message.get("params", {})
        try:
            if method == "item/tool/call":
                if self.on_tool_call is None:
                    result = {"success": False, "contentItems": [{"type": "inputText", "text": "No CAD tool handler is registered"}]}
                else:
                    try:
                        result = self.on_tool_call(params)
                        if not (isinstance(result, dict) and "success" in result and "contentItems" in result):
                            result = {"success": True, "contentItems": [{"type": "inputText", "text": json.dumps(result, ensure_ascii=False)}]}
                    except Exception as exc:
                        result = {"success": False, "contentItems": [{"type": "inputText", "text": str(exc)}]}
                self._send({"id": request_id, "result": result})
                return

            # No automatic grant of permissions or fabricated answers. Surface
            # the original request and fail closed with the protocol's deny.
            self._emit({"method": "client/actionRequired", "params": {
                "requestId": request_id, "method": method, "request": params,
                "action": "declined", "reason": "This sidebar does not automatically grant approvals or answer user questions."}})
            if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                self._send({"id": request_id, "result": {"decision": "decline"}})
            elif method == "item/permissions/requestApproval":
                self._send({"id": request_id, "result": {"permissions": {}, "scope": "turn"}})
            elif method == "mcpServer/elicitation/request":
                self._send({"id": request_id, "result": {"action": "decline", "content": None}})
            else:
                self._send({"id": request_id, "error": {"code": -32601,
                    "message": "Sidebar cannot handle this request automatically: " + method}})
        except Exception as exc:
            self._emit({"method": "client/toolResponseError", "params": {
                "method": method, "error": str(exc)}})

    def _stderr_loop(self) -> None:
        try:
            for line in self.process.stderr:
                if line.strip():
                    self._stderr.append(line.rstrip()[:2000])
        except Exception:
            pass

    def stderr_tail(self) -> list[str]:
        return list(self._stderr)

    def _fail_pending(self, reason: str) -> None:
        with self._pending_lock:
            for slot in self._pending.values():
                if not slot["event"].is_set():
                    slot["reply"] = {"error": {"message": reason}}
                    slot["event"].set()

    def close(self) -> None:
        """Close only this owned process; this does not undo CAD operations."""
        if self._closed.is_set():
            return
        self._closed.set()
        self._fail_pending("Codex client closed")
        process = self.process
        if process is None:
            return
        # Closing stdin asks stdio app-server to exit cleanly first.
        try:
            process.stdin.close()
        except Exception:
            pass
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()
