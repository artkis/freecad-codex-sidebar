"""Local OpenAI-compatible transport for FreeCAD AI and Codex ChatGPT sign-in.

Only the FreeCAD add-on executes CAD tools. This process holds a Codex dynamic
tool call until the add-on returns that exact call's result in its next HTTP
request. No OpenAI credential is read, copied, logged or sent to this listener.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import queue
import secrets
import threading
import time
import uuid

try:
    from .codex_client import CodexClient
except ImportError:  # Direct script execution by the FreeCAD launcher.
    from codex_client import CodexClient

ROOT = Path(__file__).resolve().parent
MAX_BODY = 24 * 1024 * 1024


def default_state_dir():
    override = os.environ.get("FREECAD_CODEX_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "freecad-codex-sidebar"


def default_token_path():
    return default_state_dir() / "provider.token"


DEFAULT_TOKEN = default_token_path()


class ProxyError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def text_content(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(x.get("text", "")) for x in value if isinstance(x, dict)
                         and x.get("type") in ("text", "input_text", "output_text"))
    return compact(value)


def load_token(path):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="ascii") as handle:
                handle.write(secrets.token_urlsafe(40))
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except FileExistsError:
            pass
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 24:
        raise ValueError("Provider token file is missing or invalid")
    return token


class ProviderProxy:
    def __init__(self, *, client_factory=CodexClient, tool_timeout=300,
                 turn_timeout=900, cwd=None, max_tool_calls=100):
        if cwd is None:
            raise ValueError("An explicit authorized workspace is required")
        if min(tool_timeout, turn_timeout, max_tool_calls) <= 0:
            raise ValueError("Timeouts and maximum tool calls must be positive")
        self.lock = threading.RLock()
        self.request_lock = threading.Lock()
        cwd = Path(cwd).expanduser().resolve()
        self.client = client_factory(on_notification=self.notification,
                                     on_tool_call=self.tool_call, cwd=cwd)
        self.cwd = str(cwd)
        self.tool_timeout = tool_timeout
        self.turn_timeout = turn_timeout
        self.max_tool_calls = max_tool_calls
        self.tool_call_count = 0
        self._deadline_timer = None
        self._deadline_generation = 0
        self.connected = False
        self.task_budget = None
        self.metadata = {}
        self.thread_id = None
        self.thread_permissions = None
        self.turn_id = None
        self.active = False
        self.cancelled = False
        self.needs_reconcile = False
        self.state = "starting"
        self.events = queue.Queue()
        self.pending = {}
        self.model = None
        self.effort = None
        self.system_hash = None
        self.tools_hash = None
        self.anchor = None
        self.user_count = 0
        self.user_prefix_hash = None
        self.resume_requires_append = False
        self.current_user_hash = None
        self.started_at = None
        self.total_usage = {}
        self.last_usage = {}
        self.emitted_usage = {}
        self.last_error = None
        self.last_result = ""
        self.text_parts = []
        self._message_items = set()

    def start(self):
        self.metadata = self.client.start()
        account = self.metadata.get("account", {})
        if not account.get("signedIn") or account.get("type") != "chatgpt":
            self.client.close()
            raise ProxyError("Codex is not signed in with a ChatGPT account", 503)
        self.state = "ready"
        self.connected = True
        return self.metadata

    def models(self):
        return {"object": "list", "data": [
            {"id": m.get("model") or m["id"], "object": "model", "created": 0,
             "owned_by": "codex-chatgpt", "supported_reasoning_efforts": [
                 e.get("reasoningEffort", e.get("effort")) if isinstance(e, dict) else e
                 for e in m.get("supportedReasoningEfforts", [])]}
            for m in self.metadata.get("models", [])]}

    def status(self):
        with self.lock:
            return {"ok": True, "state": self.state, "active": self.active,
                    "connected": self.connected,
                    "thread_id": self.thread_id, "turn_id": self.turn_id,
                    "model": self.model, "reasoning_effort": self.effort,
                    "thread_permissions": self.thread_permissions,
                    "account": self.metadata.get("account", {}),
                    "pending_tool_calls": [{"id": k, "name": v["name"],
                                             "delivered": v["delivered"]}
                                            for k, v in self.pending.items()],
                    "started_at": self.started_at, "usage": self.total_usage,
                    "usage_total": self.total_usage, "usage_last": self.last_usage,
                    "usage_breakdown": self.usage_breakdown(self.total_usage),
                    "workspace": self.cwd,
                    "tool_call_count": self.tool_call_count,
                    "max_tool_calls": self.max_tool_calls,
                    "effective_budget": self._budget_status(),
                    "conversation_resume": "in-process only; ephemeral threads are not restored after restart",
                    "last_error": self.last_error, "last_result": self.last_result,
                    "requires_state_inspection": self.needs_reconcile,
                    "catalog_is_entitlement_proof": False}

    def _budget_status(self):
        if self.task_budget is None:
            return None
        value = {key: self.task_budget[key] for key in
                 ("task_id", "max_seconds", "max_tool_turns", "tool_calls")}
        value["elapsed_seconds"] = max(0, time.monotonic() - self.task_budget["started_monotonic"])
        value["remaining_seconds"] = max(0, value["max_seconds"] - value["elapsed_seconds"])
        return value

    def configure_budget(self, body):
        task_id, seconds, tools = (body.get(key) for key in ("task_id", "max_seconds", "max_tool_turns"))
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 128:
            raise ProxyError("Budget requires a nonempty task_id of at most 128 characters")
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds <= 0):
            raise ProxyError("max_seconds must be finite and positive")
        if isinstance(tools, bool) or not isinstance(tools, int) or not 1 <= tools <= 500:
            raise ProxyError("max_tool_turns must be an integer from 1 to 500")
        with self.lock:
            if self.active or self.pending or self.needs_reconcile:
                raise ProxyError("Task budget can change only while idle after required inspection", 409)
            if self.task_budget and self.task_budget["task_id"] == task_id:
                if self.task_budget["max_seconds"] != seconds or self.task_budget["max_tool_turns"] != tools:
                    raise ProxyError("The current task budget cannot be changed; start a new task", 409)
                return self.status()  # Idempotent retry never renews the clock.
            self.task_budget = {"task_id": task_id, "max_seconds": float(seconds),
                "max_tool_turns": tools, "tool_calls": 0, "started_monotonic": time.monotonic()}
        return self.status()

    def _verify_thread_permissions(self, result):
        # In this app-server version environments=[] disables the local
        # workspace entirely, even when sandbox is workspace-write. Verify the
        # actual API response before allowing any model turn to start.
        def normalize(p):
            if not isinstance(p, str) or not Path(p).is_absolute():
                raise ValueError("Permission roots must be absolute paths")
            return os.path.normcase(str(Path(p).resolve()))
        policy = result.get("sandbox") or {}
        roots = result.get("runtimeWorkspaceRoots") or []
        proof = {"cwd": result.get("cwd"), "sandbox": policy,
                 "runtimeWorkspaceRoots": roots,
                 "approvalPolicy": result.get("approvalPolicy"),
                 "activePermissionProfile": result.get("activePermissionProfile")}
        self.thread_permissions = proof
        try:
            valid = (isinstance(policy, dict) and isinstance(roots, list)
                and policy.get("type") == "workspaceWrite"
                and result.get("approvalPolicy") == "never"
                and normalize(result.get("cwd")) == normalize(self.cwd)
                and {normalize(p) for p in roots} == {normalize(self.cwd)}
                and all(normalize(p) == normalize(self.cwd)
                        for p in policy.get("writableRoots", [])))
        except (TypeError, ValueError, OSError):
            valid = False
        if not valid:
            proof["verified"] = False
            raise ProxyError("Codex did not bind workspace-write to the configured private folder; no model turn started", 503)
        proof["verified"] = True

    def select_model(self, body):
        model = body.get("model")
        catalog = self.metadata.get("models", [])
        selected = next((m for m in catalog if model in (m.get("model"), m.get("id"))), None)
        if selected is None:
            raise ProxyError("Requested model is not in the current Codex catalog")
        effort = body.get("reasoning_effort") or "medium"
        supported = [e.get("reasoningEffort", e.get("effort")) if isinstance(e, dict) else e
                     for e in selected.get("supportedReasoningEfforts", [])]
        if supported and effort not in supported:
            raise ProxyError("Requested reasoning effort is not supported by this model: " +
                             ", ".join(str(x) for x in supported))
        return selected.get("model") or selected["id"], effort

    @staticmethod
    def dynamic_tools(body):
        out, names = [], set()
        for tool in body.get("tools") or []:
            if tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
                raise ProxyError("Only function tools are supported")
            f = tool["function"]
            name = f.get("name")
            if not isinstance(name, str) or not name or name in names:
                raise ProxyError("Tool names must be nonempty and unique")
            names.add(name)
            out.append({"type": "function", "name": name,
                        "description": f.get("description", ""),
                        "inputSchema": f.get("parameters") or {"type": "object", "properties": {}}})
        return out

    def prepare(self, body):
        """Called while request_lock is held; starts or resumes exactly one turn."""
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages or not all(isinstance(x, dict) for x in messages):
            raise ProxyError("messages must be a nonempty array")
        if body.get("n", 1) != 1:
            raise ProxyError("Only one completion is supported")
        model, effort = self.select_model(body)
        users = [m for m in messages if m.get("role") == "user"]
        if not users:
            raise ProxyError("A user message is required")
        current_hash = digest(users)
        tools = self.dynamic_tools(body)
        system = "\n\n".join(text_content(m.get("content")) for m in messages
                              if m.get("role") in ("system", "developer"))
        sys_hash, tool_hash, anchor = digest(system), digest(tools), digest(users[0])
        with self.lock:
            if self.needs_reconcile:
                raise ProxyError("The stopped operation needs state inspection; acknowledge with /reset before continuing", 409)
            if self.active:
                if (current_hash != self.current_user_hash or model != self.model or effort != self.effort
                        or sys_hash != self.system_hash or tool_hash != self.tools_hash):
                    raise ProxyError("Another conversation or model turn is active", 409)
                results = {m.get("tool_call_id"): m for m in messages if m.get("role") == "tool"}
                delivered = {k: p for k, p in self.pending.items() if p["delivered"]}
                if not delivered:
                    raise ProxyError("A completion request is already active", 409)
                if not all(k in results for k in delivered):
                    raise ProxyError("The exact pending tool_call_id result is required", 409)
                for call_id, pending in delivered.items():
                    raw = results[call_id].get("content", "")
                    content = text_content(raw)
                    # Tool errors must be returned to the agent as failures, not
                    # converted to success by the transport.
                    success = True
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            success = not (parsed.get("isError") is True or parsed.get("success") is False
                                           or parsed.get("ok") is False or bool(parsed.get("error")))
                    except (TypeError, ValueError):
                        pass
                    pending["response"] = {"success": success,
                                           "contentItems": [{"type": "inputText", "text": content}]}
                    self.pending.pop(call_id, None)
                    pending["event"].set()
                self.state = "running"
                return

            if self.task_budget and self._budget_status()["remaining_seconds"] <= 0:
                raise ProxyError("Task time budget is exhausted; no new model turn started", 409)
            if self.task_budget and self.task_budget["tool_calls"] >= self.task_budget["max_tool_turns"]:
                raise ProxyError("Task tool-call budget is exhausted; no new model turn started", 409)

            if self.thread_id and self.current_user_hash == current_hash:
                raise ProxyError("This user request already finished; append a new instruction or use /reset for a new conversation", 409)
            if self.resume_requires_append and (len(users) <= self.user_count
                    or digest(users[:self.user_count]) != self.user_prefix_hash
                    or self.system_hash != sys_hash or self.tools_hash != tool_hash):
                raise ProxyError("Resume must append an instruction to the unchanged conversation; use /reset to start fresh", 409)

        with self.lock:
            reuse = (self.thread_id and self.anchor == anchor and len(users) > self.user_count
                     and self.system_hash == sys_hash and self.tools_hash == tool_hash
                     and digest(users[:self.user_count]) == self.user_prefix_hash)
            previous_count = self.user_count if reuse else 0
            self.events = queue.Queue()
            self.pending = {}
            self.active = True
            self.cancelled = False
            self.state = "starting_turn"
            self.turn_id = None
            self.model, self.effort = model, effort
            self.current_user_hash = current_hash
            self.started_at = time.time()
            self.tool_call_count = 0
            self.last_error = None
            self.last_result = ""
            self.text_parts = []
            self._message_items = set()
        turn_request_sent = False
        try:
            if not reuse:
                result = self.client.request("thread/start", {
                    "model": model, "cwd": self.cwd, "ephemeral": True,
                    "allowProviderModelFallback": False,
                    "approvalPolicy": "never", "sandbox": "workspace-write",
                    "runtimeWorkspaceRoots": [self.cwd],
                    "selectedCapabilityRoots": [],
                    "dynamicTools": tools, "baseInstructions": system or
                    "You are the FreeCAD assistant. Use only the provided tools for CAD operations.",
                    "config": {"model_reasoning_effort": effort},
                })
                self._verify_thread_permissions(result)
                if result.get("model") != model:
                    raise ProxyError("Codex returned a different model; no model turn started", 503)
                with self.lock:
                    self.thread_id = result["thread"]["id"]
                    self.total_usage = {}
                    self.last_usage = {}
                    self.emitted_usage = {}
            inputs = []
            if not reuse and len(users) > 1:
                # A new app-server thread can receive an upstream conversation
                # restored from disk. Preserve the supplied history as context.
                prior = messages[:max(i for i, m in enumerate(messages) if m.get("role") == "user")]
                prior = [m for m in prior if m.get("role") not in ("system", "developer")]
                inputs.append({"type": "text", "text": "Prior conversation supplied by the client:\n" + compact(prior)})
                latest_users = users[-1:]
            else:
                latest_users = users[previous_count:]
            for msg in latest_users:
                content = msg.get("content", "")
                if isinstance(content, list):
                    for part in content:
                        if part.get("type") in ("text", "input_text"):
                            inputs.append({"type": "text", "text": part.get("text", "")})
                        elif part.get("type") == "image_url":
                            image_url = part.get("image_url", {})
                            inputs.append({"type": "image", "url": image_url.get("url") if isinstance(image_url, dict) else image_url})
                        else:
                            raise ProxyError("Unsupported user content block: " + str(part.get("type")))
                else:
                    inputs.append({"type": "text", "text": text_content(content)})
            with self.lock:
                if self.cancelled:
                    raise ProxyError("Cancelled before model execution", 409)
                if self.task_budget and self._budget_status()["remaining_seconds"] <= 0:
                    raise ProxyError("Task time budget expired before model execution", 409)
                self.anchor, self.system_hash, self.tools_hash = anchor, sys_hash, tool_hash
                self.user_count = len(users)
                self.user_prefix_hash = current_hash
                self.resume_requires_append = False
                self._start_deadline()
                turn_request_sent = True
            result = self.client.request("turn/start", {
                "threadId": self.thread_id, "model": model, "effort": effort,
                "cwd": self.cwd,
                "runtimeWorkspaceRoots": [self.cwd],
                "input": inputs,
            })
            with self.lock:
                self.turn_id = result["turn"]["id"]
                self.anchor, self.system_hash, self.tools_hash = anchor, sys_hash, tool_hash
                self.user_count = len(users)
                if self.active:
                    self.state = "running"
            if self.cancelled:
                self.cancel("Cancelled while starting")
        except Exception as exc:
            # Creating a thread does not execute a model/CAD turn. A rejected
            # turn request is also different from an unacknowledged timeout.
            # Do not leave a known pre-execution failure permanently busy.
            with self.lock:
                no_execution = not turn_request_sent or (
                    not isinstance(exc, TimeoutError) and not self.turn_id and not self.pending)
                if no_execution:
                    self.active = False
                    self._stop_deadline()
                    self.state = "failed"
                    self.last_error = str(exc)
            if not no_execution:
                self.cancel("Turn startup failed; inspect the CAD state before retrying")
            raise

    def _stop_deadline(self):
        self._deadline_generation += 1
        if self._deadline_timer:
            self._deadline_timer.cancel()
            self._deadline_timer = None

    def _start_deadline(self):
        self._stop_deadline()
        seconds = self._budget_status()["remaining_seconds"] if self.task_budget else self.turn_timeout
        timer = threading.Timer(seconds, self._deadline_expired,
                                args=(self._deadline_generation,))
        timer.daemon = True
        self._deadline_timer = timer
        timer.start()

    def _deadline_expired(self, generation):
        with self.lock:
            expired = (generation == self._deadline_generation and self.active and not self.cancelled)
        if expired:
            self.cancel("Turn time limit reached; inspect state before retrying")

    def notification(self, message):
        method, p = message.get("method"), message.get("params", {})
        with self.lock:
            if method == "client/disconnected":
                self._stop_deadline()
                self.connected = False
                self.state = "disconnected"
                if self.active:
                    self.last_error = "Codex app-server disconnected"
                    self.active = False
                    self.state = "disconnected"
                    self.needs_reconcile = True
                    for pending in self.pending.values():
                        pending["event"].set()
                    self.pending.clear()
                    self.events.put(("error", self.last_error))
                return
            if p.get("threadId") and p["threadId"] != self.thread_id:
                return
            turn = p.get("turn", {})
            incoming_turn = p.get("turnId") or turn.get("id")
            if self.turn_id and incoming_turn and incoming_turn != self.turn_id:
                return
            if method == "thread/tokenUsage/updated":
                usage = p.get("tokenUsage") or {}
                self.total_usage = usage.get("total", {})
                self.last_usage = usage.get("last", {})
                return
            if not self.active:
                return
            if method == "turn/started":
                self.turn_id = turn.get("id") or self.turn_id
                if self.cancelled:
                    threading.Thread(target=self.cancel, args=(self.last_error,), daemon=True).start()
            elif method == "item/agentMessage/delta":
                delta = p.get("delta", "")
                if delta:
                    self._message_items.add(p.get("itemId"))
                    self.text_parts.append(delta)
                    self.events.put(("text", delta))
            elif method == "item/completed":
                item = p.get("item", {})
                if item.get("type") == "agentMessage" and item.get("id") not in self._message_items:
                    value = item.get("text", "")
                    if value:
                        self._message_items.add(item.get("id"))
                        self.text_parts.append(value)
                        self.events.put(("text", value))
            elif method == "turn/completed":
                self._stop_deadline()
                self.active = False
                for pending in self.pending.values():
                    pending["event"].set()
                self.pending.clear()
                self.last_result = "".join(self.text_parts)
                status = turn.get("status", "completed")
                if status != "completed" or self.cancelled:
                    self.needs_reconcile = True
                    self.state = "cancelled" if self.cancelled or status == "interrupted" else "failed"
                    error = turn.get("error") or {}
                    self.last_error = error.get("message", "Codex turn " + status)
                    self.events.put(("error", self.last_error))
                else:
                    self.state = "ready"
                    self.events.put(("done", None))
            elif method in ("client/actionRequired", "client/toolResponseError"):
                self.events.put(("error", "Codex needs an unsupported permission or interaction; inspect /status"))

    def tool_call(self, params):
        with self.lock:
            if (not self.active or self.cancelled or params.get("threadId") != self.thread_id
                    or (self.turn_id and params.get("turnId") != self.turn_id)):
                return {"success": False, "contentItems": [{"type": "inputText", "text": "Tool call is no longer active"}]}
            call_id = params["callId"]
            exhausted = (self.task_budget and (self._budget_status()["remaining_seconds"] <= 0
                or self.task_budget["tool_calls"] >= self.task_budget["max_tool_turns"]))
            if (call_id in self.pending or exhausted
                    or (not self.task_budget and self.tool_call_count >= self.max_tool_calls)):
                threading.Thread(target=self.cancel,
                    args=("Duplicate tool call or tool-call limit reached; inspect state",), daemon=True).start()
                return {"success": False, "contentItems": [{"type": "inputText", "text": "Tool-call limit or duplicate call rejected"}]}
            self.tool_call_count += 1
            if self.task_budget:
                self.task_budget["tool_calls"] += 1
            pending = {"event": threading.Event(), "name": params["tool"],
                       "response": None, "delivered": False}
            self.pending[call_id] = pending
            self.events.put(("tool", {"id": call_id, "type": "function", "function": {
                "name": params["tool"], "arguments": compact(params.get("arguments", {}))}}))
        if not pending["event"].wait(self.tool_timeout):
            self.cancel("Timed out waiting for the FreeCAD tool result; inspect state before retrying")
        return pending["response"] or {"success": False, "contentItems": [
            {"type": "inputText", "text": "Tool result wait cancelled; inspect actual CAD state before retrying"}]}

    def next_events(self):
        while True:
            with self.lock:
                expired = (self._budget_status()["remaining_seconds"] <= 0 if self.task_budget else
                           self.started_at and time.time() - self.started_at > self.turn_timeout)
            if self.active and not self.cancelled and expired:
                self.cancel("Turn time limit reached; inspect state before retrying")
            try:
                event, value = self.events.get(timeout=10)
            except queue.Empty:
                yield "keepalive", None
                continue
            if event == "tool":
                with self.lock:
                    if self.cancelled or value["id"] not in self.pending:
                        continue
                    self.pending[value["id"]]["delivered"] = True
                    self.state = "awaiting_tool_result"
            elif event == "error" and self.active and not self.cancelled:
                self.cancel(value)
            yield event, value
            if event in ("tool", "done", "error"):
                return

    def usage(self):
        with self.lock:
            now = self.total_usage
            delta = {k: max(0, int(now.get(k, 0) or 0) - int(self.emitted_usage.get(k, 0) or 0))
                     for k in ("inputTokens", "outputTokens", "totalTokens", "cachedInputTokens",
                               "cacheWriteInputTokens", "reasoningOutputTokens")}
            self.emitted_usage = dict(now)
        return {"prompt_tokens": delta["inputTokens"], "completion_tokens": delta["outputTokens"],
                "total_tokens": delta["totalTokens"],
                "prompt_tokens_details": {"cached_tokens": delta["cachedInputTokens"],
                    "uncached_tokens": max(0, delta["inputTokens"] - delta["cachedInputTokens"]),
                    "cache_write_tokens": delta["cacheWriteInputTokens"]},
                "completion_tokens_details": {"reasoning_tokens": delta["reasoningOutputTokens"]}}

    @staticmethod
    def usage_breakdown(usage):
        inputs = int(usage.get("inputTokens", 0) or 0)
        cached = int(usage.get("cachedInputTokens", 0) or 0)
        return {"input_tokens": inputs, "cached_input_tokens": cached,
                "uncached_input_tokens": max(0, inputs - cached),
                "output_tokens": int(usage.get("outputTokens", 0) or 0),
                "total_tokens": int(usage.get("totalTokens", 0) or 0)}

    def cancel(self, reason="Cancelled by controller"):
        with self.lock:
            self._stop_deadline()
            was_active = self.active
            self.cancelled = True
            self.needs_reconcile = bool(was_active or self.pending or self.needs_reconcile)
            self.state = "cancelling" if was_active else "cancelled"
            self.last_error = reason
            thread_id, turn_id = self.thread_id, self.turn_id
            for pending in self.pending.values():
                pending["event"].set()
            self.pending.clear()
            self.events.put(("error", reason))
        if was_active and thread_id and turn_id:
            try:
                self.client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=10)
            except Exception:
                with self.lock:
                    self.state = "cancel_requested_unconfirmed"
        return self.status()

    def steer(self, text, expected_turn_id):
        if not isinstance(text, str) or not text.strip() or len(text) > 64000:
            raise ProxyError("Steering requires nonempty text of at most 64000 characters")
        with self.lock:
            if (not self.active or self.cancelled or not self.turn_id
                    or expected_turn_id != self.turn_id):
                raise ProxyError("Steering requires the exact active expected_turn_id", 409)
            thread_id, turn_id = self.thread_id, self.turn_id
        result = self.client.request("turn/steer", {"threadId": thread_id,
            "expectedTurnId": turn_id, "input": [{"type": "text", "text": text}]}, timeout=15)
        if result.get("turnId") != turn_id:
            raise ProxyError("Codex returned a different steering turn id; inspect /status", 502)
        return {"ok": True, "turn_id": turn_id, "state": self.status()["state"]}

    def resume(self, acknowledged=False):
        """Retain an interrupted in-process conversation after explicit inspection.

        This never retries a CAD call or starts a model turn. The next completion
        must append a new user instruction; disk/cross-process resume is absent.
        """
        with self.lock:
            if self.active:
                raise ProxyError("Wait for confirmed turn completion before resuming", 409)
            if not acknowledged or not self.needs_reconcile:
                raise ProxyError("Resume requires stopped work and acknowledge_state_inspected=true", 409)
            if not self.thread_id or not (self.thread_permissions or {}).get("verified"):
                raise ProxyError("No verified in-process conversation is available; use /reset", 409)
            if not self.connected:
                raise ProxyError("The provider disconnected; its ephemeral conversation cannot be resumed", 409)
            self.cancelled = False
            self.needs_reconcile = False
            self.resume_requires_append = True
            self.events = queue.Queue()
            self.state = "ready"
        return self.status()

    def reset(self, acknowledged=False):
        with self.lock:
            if self.active:
                raise ProxyError("The previous Codex turn has not finished stopping", 409)
            if self.needs_reconcile and not acknowledged:
                raise ProxyError("Inspect actual CAD state, then acknowledge_state_inspected=true", 409)
            self.thread_id = None
            self.thread_permissions = None
            self.turn_id = None
            self.anchor = None
            self.user_count = 0
            self.user_prefix_hash = None
            self.resume_requires_append = False
            self.task_budget = None
            self.pending.clear()
            self.events = queue.Queue()
            self.cancelled = False
            self.needs_reconcile = False
            self.state = "ready"
        return self.status()

    def close(self):
        self.cancel("Provider shutting down")
        self.client.close()


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FreeCADCodex/0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_):
        pass  # Never log request headers, prompts or tokens.

    def authorized(self):
        expected = "Bearer " + self.server.token
        return hmac.compare_digest(self.headers.get("Authorization", "").encode("utf-8"),
                                   expected.encode("utf-8"))

    def json_reply(self, code, value):
        data = compact(value).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.authorized():
            return self.json_reply(401, {"error": {"message": "Local bearer authentication required"}})
        path = self.path.split("?", 1)[0]
        if path == "/v1/models":
            return self.json_reply(200, self.server.proxy.models())
        if path in ("/status", "/v1/status"):
            return self.json_reply(200, self.server.proxy.status())
        self.json_reply(404, {"error": {"message": "Unknown endpoint"}})

    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ProxyError("Invalid Content-Length")
        if length < 0 or length > MAX_BODY:
            raise ProxyError("Request body exceeds limit", 413)
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise ProxyError("Invalid JSON body")
        if not isinstance(value, dict):
            raise ProxyError("JSON body must be an object")
        return value

    def do_POST(self):
        if not self.authorized():
            self.close_connection = True
            return self.json_reply(401, {"error": {"message": "Local bearer authentication required"}})
        proxy = self.server.proxy
        path = self.path.split("?", 1)[0]
        try:
            body = self.body()
            if path in ("/cancel", "/v1/cancel"):
                return self.json_reply(200, proxy.cancel())
            if path in ("/reset", "/v1/reset"):
                return self.json_reply(200, proxy.reset(body.get("acknowledge_state_inspected") is True))
            if path in ("/resume", "/v1/resume"):
                return self.json_reply(200, proxy.resume(body.get("acknowledge_state_inspected") is True))
            if path in ("/steer", "/v1/steer"):
                return self.json_reply(200, proxy.steer(body.get("text"), body.get("expected_turn_id")))
            if path in ("/budget", "/v1/budget"):
                return self.json_reply(200, proxy.configure_budget(body))
            if path != "/v1/chat/completions":
                return self.json_reply(404, {"error": {"message": "Unknown endpoint"}})
            if not proxy.request_lock.acquire(blocking=False):
                raise ProxyError("Another completion request is active", 409)
            try:
                proxy.prepare(body)
                if body.get("stream", False):
                    self.stream_reply()
                else:
                    self.complete_reply()
            finally:
                proxy.request_lock.release()
        except ProxyError as exc:
            self.json_reply(exc.status, {"error": {"message": str(exc), "type": "invalid_request_error"}})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            proxy.cancel("Client disconnected; inspect CAD state before retrying")
        except Exception as exc:
            proxy.cancel("Provider operation failed")
            self.json_reply(502, {"error": {"message": str(exc), "type": "provider_error"}})

    def stream_reply(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        request_id = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())

        def send(delta=None, finish=None, extra=None):
            data = {"id": request_id, "object": "chat.completion.chunk", "created": created,
                    "model": self.server.proxy.model,
                    "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
            if extra:
                data.update(extra)
            self.wfile.write(("data: " + compact(data) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        send({"role": "assistant"})
        for event, value in self.server.proxy.next_events():
            if event == "keepalive":
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
            elif event == "text":
                send({"content": value})
            elif event == "tool":
                send({"tool_calls": [{"index": 0, **value}]})
                send(finish="tool_calls", extra={"usage": self.server.proxy.usage()})
            elif event == "done":
                send(finish="stop", extra={"usage": self.server.proxy.usage()})
            elif event == "error":
                self.wfile.write(("data: " + compact({"error": {"message": value,
                                  "type": "provider_error", "code": 502}}) + "\n\n").encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def complete_reply(self):
        content, calls = [], []
        for event, value in self.server.proxy.next_events():
            if event == "text":
                content.append(value)
            elif event == "tool":
                calls.append(value)
            elif event == "error":
                raise ProxyError(value, 502)
        message = {"role": "assistant", "content": "".join(content)}
        if calls:
            message["tool_calls"] = calls
        self.json_reply(200, {"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion",
                             "created": int(time.time()), "model": self.server.proxy.model,
                             "choices": [{"index": 0, "message": message,
                                          "finish_reason": "tool_calls" if calls else "stop"}],
                             "usage": self.server.proxy.usage()})


def make_server(proxy, token, port=9876):
    server = ThreadingHTTPServer(("127.0.0.1", port), ProxyHandler)
    server.daemon_threads = True
    server.proxy = proxy
    server.token = token
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN)
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Authorized native-file workspace for this provider session")
    parser.add_argument("--tool-timeout", type=float, default=300)
    parser.add_argument("--turn-timeout", type=float, default=900)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or min(args.tool_timeout, args.turn_timeout) <= 0:
        parser.error("Invalid port or timeout")
    args.workspace = args.workspace.expanduser().resolve()
    args.workspace.mkdir(parents=True, exist_ok=True)
    token = load_token(args.token_file)
    proxy = ProviderProxy(tool_timeout=args.tool_timeout, turn_timeout=args.turn_timeout,
                          cwd=args.workspace)
    server = make_server(proxy, token, args.port)
    try:
        proxy.start()
        print(compact({"status": "ready", "host": "127.0.0.1", "port": args.port,
                       "auth": "Codex ChatGPT", "model_count": len(proxy.metadata.get("models", []))}), flush=True)
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        proxy.close()


if __name__ == "__main__":
    main()
