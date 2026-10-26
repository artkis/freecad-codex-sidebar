"""Backend contract tests: no Codex inference, CAD, auth or desktop interaction."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "freecad_codex"))
from codex_client import CodexClient, CodexError, discover_binary, _runtime_version
from provider_proxy import ProviderProxy, ProxyError, default_token_path, load_token


class FakeClient:
    def __init__(self, **kwargs):
        self.notify = kwargs["on_notification"]
        self.tool = kwargs["on_tool_call"]
        self.cwd = str(kwargs["cwd"])
        self.requests = []
        self.permission_change = None
        self.turn_error = None
        self.closed = False
        self.turn_number = 0

    def start(self):
        return {"account": {"signedIn": True, "type": "chatgpt"}, "models": [
            {"id": "test-model", "model": "test-model", "supportedReasoningEfforts": [
                {"reasoningEffort": "medium"}, {"reasoningEffort": "high"}]}]}

    def request(self, method, params, **kwargs):
        self.requests.append((method, copy.deepcopy(params)))
        if method == "thread/start":
            value = {"thread": {"id": "thread-" + str(len(self.requests))},
                "model": params["model"], "cwd": self.cwd, "approvalPolicy": "never",
                "sandbox": {"type": "workspaceWrite", "writableRoots": []},
                "runtimeWorkspaceRoots": [self.cwd]}
            if self.permission_change:
                self.permission_change(value)
            return value
        if method == "turn/start":
            if self.turn_error:
                raise self.turn_error
            self.turn_number += 1
            return {"turn": {"id": "turn-" + str(self.turn_number)}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    def close(self):
        self.closed = True


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.proxy = ProviderProxy(client_factory=FakeClient, cwd=self.directory.name,
                                   tool_timeout=1, turn_timeout=10)
        self.proxy.start()
        self.body = {"model": "test-model", "reasoning_effort": "medium", "messages": [
            {"role": "system", "content": "Stable CAD instructions"},
            {"role": "user", "content": "Build this bounded fixture"}],
            "tools": [{"type": "function", "function": {"name": "cad_tool",
                "parameters": {"type": "object", "properties": {}}}}]}

    def tearDown(self):
        self.proxy.close()
        self.directory.cleanup()

    def completed(self, status="completed"):
        self.proxy.notification({"method": "turn/completed", "params": {
            "threadId": self.proxy.thread_id, "turn": {"id": self.proxy.turn_id, "status": status}}})

    def test_workspace_is_explicit_and_permissions_checked_before_inference(self):
        self.proxy.prepare(self.body)
        first = self.proxy.client.requests[0][1]
        turn = self.proxy.client.requests[1][1]
        self.assertNotIn("environments", first)
        self.assertNotIn("environments", turn)
        self.assertEqual(first["runtimeWorkspaceRoots"], [self.proxy.cwd])
        self.assertEqual(turn["runtimeWorkspaceRoots"], [self.proxy.cwd])
        self.assertEqual(first["baseInstructions"], "Stable CAD instructions")
        self.assertTrue(self.proxy.status()["thread_permissions"]["verified"])

    def test_wrong_missing_extra_or_relative_roots_reject_before_turn(self):
        for roots in ([], [str(Path(self.proxy.cwd).parent)], [self.proxy.cwd, str(Path(self.proxy.cwd).parent)],
                      ["relative"], [None], "invalid"):
            with self.subTest(roots=roots):
                self.proxy.client.permission_change = lambda x: x.update(runtimeWorkspaceRoots=roots)
                self.proxy.client.requests.clear()
                with self.assertRaises(ProxyError):
                    self.proxy.prepare(self.body)
                self.assertEqual([r[0] for r in self.proxy.client.requests], ["thread/start"])
                self.assertFalse(self.proxy.active)
                self.assertFalse(self.proxy.thread_permissions["verified"])

    def test_broader_write_root_rejected(self):
        self.proxy.client.permission_change = lambda x: x["sandbox"].update(
            writableRoots=[str(Path(self.proxy.cwd).parent)])
        with self.assertRaises(ProxyError):
            self.proxy.prepare(self.body)
        self.assertFalse(any(m == "turn/start" for m, _ in self.proxy.client.requests))

    def test_model_mismatch_rejected_before_turn(self):
        self.proxy.client.permission_change = lambda x: x.update(model="another-model")
        with self.assertRaises(ProxyError):
            self.proxy.prepare(self.body)
        self.assertEqual(len(self.proxy.client.requests), 1)

    def test_exact_tool_result_continues_same_logical_turn_and_preserves_failure(self):
        self.proxy.prepare(self.body)
        returned = []
        worker = threading.Thread(target=lambda: returned.append(self.proxy.tool_call({
            "threadId": self.proxy.thread_id, "turnId": self.proxy.turn_id,
            "callId": "call-a", "tool": "cad_tool", "arguments": {"x": 1}})))
        worker.start()
        event, call = next(self.proxy.next_events())
        self.assertEqual((event, call["id"]), ("tool", "call-a"))
        body = copy.deepcopy(self.body)
        body["messages"].append({"role": "tool", "tool_call_id": "wrong", "content": "{}"})
        with self.assertRaises(ProxyError):
            self.proxy.prepare(body)
        body["messages"][-1] = {"role": "tool", "tool_call_id": "call-a",
                                "content": '{"success":false,"error":"bad geometry"}'}
        changed = copy.deepcopy(body)
        changed["messages"][0]["content"] = "A different system"
        with self.assertRaises(ProxyError):
            self.proxy.prepare(changed)
        self.proxy.prepare(body)
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertFalse(returned[0]["success"])
        self.assertEqual([m for m, _ in self.proxy.client.requests], ["thread/start", "turn/start"])

    def test_followup_reuses_thread_without_replaying_history(self):
        self.proxy.prepare(self.body)
        thread = self.proxy.thread_id
        self.completed()
        self.body["messages"].extend([{"role": "assistant", "content": "Fixture built"},
                                      {"role": "user", "content": "Now check it"}])
        self.proxy.prepare(self.body)
        self.assertEqual(self.proxy.thread_id, thread)
        self.assertEqual([m for m, _ in self.proxy.client.requests], ["thread/start", "turn/start", "turn/start"])
        self.assertEqual(self.proxy.client.requests[-1][1]["input"], [{"type": "text", "text": "Now check it"}])

    def test_changed_prior_user_history_does_not_reuse_old_context(self):
        self.proxy.prepare(self.body)
        self.completed()
        self.body["messages"].append({"role": "user", "content": "Second"})
        self.proxy.prepare(self.body)
        self.completed()
        self.body["messages"][-1]["content"] = "Changed second"
        self.body["messages"].append({"role": "user", "content": "Third"})
        self.proxy.prepare(self.body)
        self.assertEqual(sum(m == "thread/start" for m, _ in self.proxy.client.requests), 2)

    def test_duplicate_finished_request_is_not_replayed(self):
        self.proxy.prepare(self.body)
        self.completed()
        with self.assertRaises(ProxyError):
            self.proxy.prepare(self.body)
        self.assertEqual(sum(m == "turn/start" for m, _ in self.proxy.client.requests), 1)

    def test_usage_keeps_last_total_cached_and_uncached_separate(self):
        self.proxy.prepare(self.body)
        total = {"inputTokens": 3427778, "cachedInputTokens": 3319040,
                 "outputTokens": 15534, "totalTokens": 3443312, "reasoningOutputTokens": 100}
        last = {"inputTokens": 500, "cachedInputTokens": 400, "outputTokens": 20, "totalTokens": 520}
        self.proxy.notification({"method": "thread/tokenUsage/updated", "params": {
            "threadId": self.proxy.thread_id, "turnId": self.proxy.turn_id,
            "tokenUsage": {"total": total, "last": last}}})
        status = self.proxy.status()
        self.assertEqual(status["usage_total"], total)
        self.assertEqual(status["usage_last"], last)
        self.assertEqual(status["usage_breakdown"]["uncached_input_tokens"], 108738)
        usage = self.proxy.usage()
        self.assertEqual(usage["prompt_tokens_details"]["cached_tokens"], 3319040)
        self.assertEqual(usage["prompt_tokens_details"]["uncached_tokens"], 108738)
        self.assertEqual(self.proxy.usage()["total_tokens"], 0)
        # Repeated cumulative notifications are never double counted.
        self.assertEqual(self.proxy.status()["usage_total"], total)

    def test_old_turn_notifications_are_ignored(self):
        self.proxy.prepare(self.body)
        self.proxy.notification({"method": "turn/completed", "params": {
            "threadId": self.proxy.thread_id, "turn": {"id": "old-turn", "status": "completed"}}})
        self.assertTrue(self.proxy.active)

    def test_steer_requires_exact_active_turn_and_never_restarts(self):
        self.proxy.prepare(self.body)
        with self.assertRaises(ProxyError):
            self.proxy.steer("Use the corrected dimension", "wrong")
        turn = self.proxy.turn_id
        self.assertEqual(self.proxy.steer("Use the corrected dimension", turn)["turn_id"], turn)
        self.assertEqual(self.proxy.client.requests[-1], ("turn/steer", {
            "threadId": self.proxy.thread_id, "expectedTurnId": turn,
            "input": [{"type": "text", "text": "Use the corrected dimension"}]}))
        self.assertFalse(self.proxy.client.closed)

    def test_cancel_requires_confirmed_stop_then_inspection_before_resume(self):
        self.proxy.prepare(self.body)
        turn = self.proxy.turn_id
        self.proxy.cancel()
        self.assertTrue(self.proxy.active)  # Interrupt acknowledgement is not completion.
        with self.assertRaises(ProxyError):
            self.proxy.resume(True)
        self.completed("interrupted")
        with self.assertRaises(ProxyError):
            self.proxy.resume(False)
        count = len(self.proxy.client.requests)
        self.proxy.resume(True)
        self.assertEqual(len(self.proxy.client.requests), count)
        with self.assertRaises(ProxyError):
            self.proxy.prepare(self.body)
        self.body["messages"].append({"role": "user", "content": "State inspected; continue from here"})
        self.proxy.prepare(self.body)
        self.assertEqual(sum(m == "thread/start" for m, _ in self.proxy.client.requests), 1)
        self.assertNotEqual(self.proxy.turn_id, turn)

    def test_deadline_runs_even_without_an_http_consumer(self):
        self.proxy.turn_timeout = 0.03
        self.proxy.prepare(self.body)
        deadline = time.monotonic() + 1
        while not self.proxy.cancelled and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.proxy.cancelled)
        self.assertTrue(self.proxy.needs_reconcile)
        self.assertTrue(any(m == "turn/interrupt" for m, _ in self.proxy.client.requests))

    def test_budget_is_idle_only_validated_and_idempotent_without_clock_renewal(self):
        budget = {"task_id": "task-a", "max_seconds": 1200, "max_tool_turns": 200}
        with patch("provider_proxy.time.monotonic", return_value=100):
            self.proxy.configure_budget(budget)
        with patch("provider_proxy.time.monotonic", return_value=160):
            effective = self.proxy.configure_budget(budget)["effective_budget"]
        self.assertEqual(effective["remaining_seconds"], 1140)
        self.assertEqual(effective["max_tool_turns"], 200)
        with self.assertRaises(ProxyError):
            self.proxy.configure_budget({**budget, "max_seconds": 2000})
        self.proxy.reset()
        self.proxy.configure_budget(budget)
        self.proxy.prepare(self.body)
        with self.assertRaises(ProxyError):
            self.proxy.configure_budget({**budget, "task_id": "another"})
        for invalid in ({"max_seconds": float("nan")}, {"max_seconds": float("inf")},
                        {"max_seconds": 0}, {"max_seconds": True},
                        {"max_tool_turns": 501}, {"max_tool_turns": True}, {"max_tool_turns": 1.5}):
            with self.subTest(invalid=invalid), self.assertRaises(ProxyError):
                self.proxy.configure_budget({**budget, **invalid})

    def test_task_budget_counts_tools_across_logical_turns(self):
        self.proxy.configure_budget({"task_id": "task-a", "max_seconds": 30, "max_tool_turns": 2})
        self.proxy.prepare(self.body)

        def execute_tool(call_id):
            result = []
            worker = threading.Thread(target=lambda: result.append(self.proxy.tool_call({
                "threadId": self.proxy.thread_id, "turnId": self.proxy.turn_id,
                "callId": call_id, "tool": "cad_tool", "arguments": {}})))
            worker.start()
            self.assertEqual(next(self.proxy.next_events())[0], "tool")
            response = copy.deepcopy(self.body)
            response["messages"].append({"role": "tool", "tool_call_id": call_id, "content": "{}"})
            self.proxy.prepare(response)
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertTrue(result[0]["success"])

        execute_tool("first")
        self.completed()
        self.body["messages"].append({"role": "user", "content": "Check the complete build"})
        self.proxy.prepare(self.body)
        execute_tool("second")
        self.assertEqual(self.proxy.status()["effective_budget"]["tool_calls"], 2)
        rejected = self.proxy.tool_call({"threadId": self.proxy.thread_id, "turnId": self.proxy.turn_id,
            "callId": "third", "tool": "cad_tool", "arguments": {}})
        self.assertFalse(rejected["success"])
        self.assertNotIn("third", self.proxy.pending)

    def test_task_time_budget_spans_turns_and_expires_before_next_inference(self):
        self.proxy.configure_budget({"task_id": "task-a", "max_seconds": 30, "max_tool_turns": 10})
        self.proxy.prepare(self.body)
        self.completed()
        count = len(self.proxy.client.requests)
        self.body["messages"].append({"role": "user", "content": "Another phase"})
        with patch("provider_proxy.time.monotonic", return_value=time.monotonic() + 31):
            with self.assertRaises(ProxyError):
                self.proxy.prepare(self.body)
        self.assertEqual(len(self.proxy.client.requests), count)

    def test_configured_task_deadline_overrides_default_per_turn_timeout(self):
        self.proxy.configure_budget({"task_id": "task-a", "max_seconds": 0.03, "max_tool_turns": 10})
        self.proxy.prepare(self.body)
        deadline = time.monotonic() + 1
        while not self.proxy.cancelled and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.proxy.cancelled)
        self.assertTrue(self.proxy.needs_reconcile)

    def test_rejected_turn_is_known_preexecution_failure_but_timeout_is_uncertain(self):
        self.proxy.client.turn_error = CodexError("bad parameter")
        with self.assertRaises(CodexError):
            self.proxy.prepare(self.body)
        self.assertFalse(self.proxy.active)
        self.assertFalse(self.proxy.needs_reconcile)
        self.proxy.reset()
        self.proxy.client.turn_error = TimeoutError("No acknowledgement")
        with self.assertRaises(TimeoutError):
            self.proxy.prepare(self.body)
        self.assertTrue(self.proxy.active)
        self.assertTrue(self.proxy.needs_reconcile)

    def test_disconnection_releases_waiting_tool_and_prevents_resume(self):
        self.proxy.prepare(self.body)
        result = []
        worker = threading.Thread(target=lambda: result.append(self.proxy.tool_call({
            "threadId": self.proxy.thread_id, "turnId": self.proxy.turn_id,
            "callId": "call-a", "tool": "cad_tool", "arguments": {}})))
        worker.start()
        next(self.proxy.next_events())
        self.proxy.notification({"method": "client/disconnected", "params": {}})
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertFalse(result[0]["success"])
        with self.assertRaises(ProxyError):
            self.proxy.resume(True)


class TransportTests(unittest.TestCase):
    def test_start_reads_only_metadata_redacts_identity_and_inherits_route(self):
        class Process:
            stdin, stdout, stderr = io.StringIO(), io.StringIO(), io.StringIO()
            def poll(self):
                return None
            def wait(self, **_):
                return 0
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "codex-test.exe"
            binary.write_bytes(b"not executed")
            client = CodexClient(binary=binary, cwd=folder)
            responses = [{"version": "test"}, {"account": {"type": "chatgpt", "planType": "pro",
                "email": "never retained", "secret": "never retained"}},
                {"data": [{"id": "model-a"}], "nextCursor": None}]
            with patch("codex_client.subprocess.Popen", return_value=Process()) as launch, \
                 patch.object(client, "_read_loop"), patch.object(client, "_stderr_loop"), \
                 patch.object(client, "_mcp_names", return_value=[]), \
                 patch.object(client, "request", side_effect=responses) as requests, \
                 patch.dict(os.environ, {"HINDSIGHT_BANK_ID": "route-from-launcher"}):
                metadata = client.start()
                self.assertEqual([call.args[0] for call in requests.call_args_list],
                                 ["initialize", "account/read", "model/list"])
                self.assertNotIn("email", metadata["account"])
                self.assertNotIn("secret", metadata["account"])
                self.assertEqual(launch.call_args.kwargs["env"]["HINDSIGHT_BANK_ID"], "route-from-launcher")
                self.assertEqual(launch.call_args.kwargs["cwd"], folder)
                client.close()

    def test_reader_correlates_reply_and_handles_non_json_without_pending_leak(self):
        class Process:
            stdout = io.StringIO('not JSON\n{"id":1,"result":{"ok":true}}\n')
            def poll(self):
                return None
        client = CodexClient()
        client.process = Process()
        with patch.object(client, "_send", side_effect=lambda _: client._read_loop()):
            self.assertEqual(client.request("test", {}, timeout=0.1), {"ok": True})
        self.assertEqual(client._pending, {})
        self.assertTrue(any("Non-JSON" in line for line in client.stderr_tail()))

    def test_explicit_runtime_and_invalid_override(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "codex-test.exe"
            binary.write_bytes(b"not executed")
            self.assertEqual(discover_binary(binary), binary.resolve())
            with patch.dict(os.environ, {"FREECAD_CODEX_BINARY": str(binary / "missing")}):
                with self.assertRaises(CodexError):
                    discover_binary()

    def test_build_directory_discovery_has_no_pinned_user_or_version(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "OpenAI" / "Codex" / "bin"
            old = root / "old-build" / "codex.exe"
            new = root / "new-build" / "codex.exe"
            for path in (old, new):
                path.parent.mkdir(parents=True)
                path.write_bytes(b"not executed")
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            with patch.dict(os.environ, {"LOCALAPPDATA": folder}, clear=True), \
                 patch("codex_client.shutil.which", return_value=None), \
                 patch("codex_client._runtime_version", side_effect=lambda p: (0, 2 if p == new else 1, 0)):
                self.assertEqual(discover_binary(), new.resolve())

    def test_desktop_runtime_beats_older_path_and_invalid_parent_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "OpenAI" / "Codex" / "bin"
            launcher = root / "codex.exe"
            old = root / "older-build" / "codex.exe"
            new = root / "newer-build" / "codex.exe"
            global_cli = Path(folder) / "npm" / "codex.exe"
            for path in (old, new, launcher, global_cli):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"not executed")
            # Modification time alone must not win over a newer CLI version.
            os.utime(new, (1, 1))
            versions = {old: (0, 160, 0), new: (0, 164, 0),
                        launcher: None, global_cli: (0, 128, 0)}
            with patch.dict(os.environ, {"LOCALAPPDATA": folder}, clear=True), \
                 patch("codex_client.shutil.which", return_value=str(global_cli)), \
                 patch("codex_client._runtime_version", side_effect=versions.get) as probe:
                self.assertEqual(discover_binary(), new.resolve())
                self.assertEqual({call.args[0] for call in probe.call_args_list}, {old, new})

    def test_version_probe_only_runs_bounded_version_command(self):
        from types import SimpleNamespace
        with patch("codex_client.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout="codex-cli 0.164.0-alpha.11.1\n")) as run:
            self.assertEqual(_runtime_version("codex-test"), (0, 164, 0, False, "alpha", (11, 1)))
            self.assertEqual(run.call_args.args[0], ["codex-test", "--version"])
            self.assertEqual(run.call_args.kwargs["timeout"], 2)
        with patch("codex_client.subprocess.run", side_effect=subprocess.TimeoutExpired("codex-test", 2)):
            self.assertIsNone(_runtime_version("codex-test"))

    def test_package_module_and_direct_script_help_import_without_runtime(self):
        source = Path(__file__).resolve().parents[1] / "src"
        env = dict(os.environ, PYTHONPATH=str(source))
        commands = ([sys.executable, "-m", "freecad_codex.provider_proxy", "--help"],
                    [sys.executable, str(source / "freecad_codex" / "provider_proxy.py"), "--help"])
        for command in commands:
            with self.subTest(command=command[1]):
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--workspace", result.stdout)

    def test_scoped_child_settings_do_not_force_memory_bank(self):
        client = CodexClient(binary="codex-test.exe")
        with patch.object(client, "_mcp_names", return_value=["example"]), patch.dict(os.environ, {"HINDSIGHT_BANK_ID": "test-inherited"}):
            args = client._command()
        self.assertIn("shell_tool", args)
        self.assertIn("mcp_servers.example.enabled=false", args)
        self.assertEqual(client.diagnostics["hindsight_bank"], "test-inherited")
        self.assertNotIn("model=", " ".join(args))

    def test_request_timeout_cleans_pending_without_claiming_turn_cancelled(self):
        client = CodexClient()
        with patch.object(client, "_send"):
            with self.assertRaisesRegex(TimeoutError, "does not cancel"):
                client.request("turn/start", {}, timeout=0.001)
        self.assertEqual(client._pending, {})

    def test_server_request_returns_tool_failure_and_declines_approval(self):
        client = CodexClient(on_tool_call=lambda _: {"success": False,
            "contentItems": [{"type": "inputText", "text": "tool rejected"}]})
        sent = []
        with patch.object(client, "_send", side_effect=sent.append):
            client._handle_server_request({"id": 1, "method": "item/tool/call", "params": {}})
            client._handle_server_request({"id": 2, "method": "item/fileChange/requestApproval", "params": {}})
        self.assertFalse(sent[0]["result"]["success"])
        self.assertEqual(sent[1]["result"], {"decision": "decline"})

    def test_local_token_is_stable_and_defaults_outside_source(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"FREECAD_CODEX_STATE_DIR": folder}):
            token_path = default_token_path()
            self.assertEqual(token_path.parent, Path(folder).resolve())
            token = load_token(token_path)
            self.assertGreaterEqual(len(token), 24)
            self.assertEqual(load_token(token_path), token)


if __name__ == "__main__":
    unittest.main()
