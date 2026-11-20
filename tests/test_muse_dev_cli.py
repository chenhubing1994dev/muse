from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import muse_dev
from muse.task_queue import TaskQueue


class MuseDevCliTests(unittest.TestCase):
    def test_run_forwards_project_relative_script_and_options(self):
        expected = {"status": "completed", "exit_code": 0, "transport": "psmux-detached-window"}
        with patch.object(muse_dev.adapter, "execute_request", return_value=expected) as execute:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = muse_dev.main([
                    "run", "--language", "python", "--script-path", "scripts/check.py",
                    "--cwd", "src", "--session", "muse-dev", "--timeout", "45",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), expected)
        request = execute.call_args.args[0]
        self.assertEqual(request["script_path"], "scripts/check.py")
        self.assertEqual(request["expected_cwd"], "src")
        self.assertEqual(request["language"], "python")
        self.assertEqual(request["timeout_secs"], 45)
        self.assertEqual(execute.call_args.kwargs["project_root"], str(muse_dev.PROJECT_ROOT))

    def test_run_forwards_inline_script_without_shell_interpolation(self):
        with patch.object(
            muse_dev.adapter, "execute_request", return_value={"status": "completed", "exit_code": 0}
        ) as execute:
            with contextlib.redirect_stdout(io.StringIO()):
                code = muse_dev.main(["run", "--script", "print('safe $value')", "--language", "python"])
        self.assertEqual(code, 0)
        self.assertEqual(execute.call_args.args[0]["script"], "print('safe $value')")

    def test_script_path_without_language_leaves_extension_inference_to_adapter(self):
        with patch.object(
            muse_dev.adapter, "execute_request", return_value={"status": "completed", "exit_code": 0}
        ) as execute:
            with contextlib.redirect_stdout(io.StringIO()):
                code = muse_dev.main(["run", "--script-path", "scripts/check.py"])
        self.assertEqual(code, 0)
        self.assertNotIn("language", execute.call_args.args[0])

    def test_run_propagates_real_nonzero_exit_code(self):
        with patch.object(
            muse_dev.adapter, "execute_request", return_value={"status": "completed", "exit_code": 7}
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(muse_dev.main(["run", "--script", "exit 7"]), 7)

    def test_unknown_outcome_has_distinct_exit_code(self):
        with patch.object(
            muse_dev.adapter, "execute_request", return_value={"status": "outcome_unknown"}
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(muse_dev.main(["run", "--script", "echo wait"]), 3)

    def test_inspect_and_recover_use_existing_adapter_actions(self):
        with patch.object(muse_dev.adapter, "inspect_session", return_value={"status": "completed"}) as inspect, patch.object(
            muse_dev.adapter, "recover_job", return_value={"status": "completed", "exit_code": 0}
        ) as recover:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(muse_dev.main(["inspect", "--session", "muse-dev"]), 0)
                self.assertEqual(muse_dev.main(["recover", "a" * 32]), 0)
        inspect.assert_called_once_with({"session": "muse-dev"})
        recover.assert_called_once_with("a" * 32)

    def test_list_delegates_read_only_job_inventory_to_adapter(self):
        expected = {"status": "completed", "read_only": True, "jobs": []}
        with patch.object(muse_dev.adapter, "list_jobs", return_value=expected) as listing:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = muse_dev.main(["list", "--session", "muse-dev", "--limit", "12"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), expected)
        listing.assert_called_once_with({"session": "muse-dev", "limit": 12})

    def test_run_requires_exactly_one_script_source(self):
        with self.assertRaises(SystemExit):
            muse_dev.main(["run"])
        with self.assertRaises(SystemExit):
            muse_dev.main(["run", "--script", "echo x", "--script-path", "scripts/x.py"])

    def test_commander_status_and_list_delegate_to_existing_queue_bridge(self):
        with patch.object(muse_dev.chatgpt_bridge, "command_task_status", return_value=0) as status, patch.object(
            muse_dev.chatgpt_bridge, "command_task_list", return_value=0
        ) as listing:
            self.assertEqual(muse_dev.main(["commander", "status", "--task-id", "ticket-1"]), 0)
            self.assertEqual(muse_dev.main(["commander", "list", "--limit", "7"]), 0)
        self.assertEqual(status.call_args.args[0].task_id, "ticket-1")
        self.assertEqual(listing.call_args.args[0].limit, 7)

    def test_commander_submit_requires_stable_idempotency_key_and_never_dispatches(self):
        with patch.object(muse_dev.chatgpt_bridge, "command_submit", return_value=0) as submit:
            self.assertEqual(muse_dev.main([
                "commander", "submit", "--request", "Implement safe bridge",
                "--request-id", "bridge-issue-42",
            ]), 0)
        args = submit.call_args.args[0]
        self.assertEqual(args.request_id, "bridge-issue-42")
        self.assertEqual(args.database, str(muse_dev.chatgpt_bridge.DEFAULT_DATABASE))

    def test_commander_approve_is_only_queue_approval(self):
        with patch.object(muse_dev.chatgpt_bridge, "command_approve", return_value=0) as approve:
            self.assertEqual(muse_dev.main([
                "commander", "approve", "--task-id", "ticket-1", "--approved-by", "user",
            ]), 0)
        self.assertEqual(approve.call_args.args[0].task_id, "ticket-1")

    def test_commander_cli_end_to_end_reuses_one_approval_gated_taskqueue_ticket(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "tasks.sqlite3")
            request = "Build a safe local developer bridge"
            request_id = "e2e-commander-bridge-20261120"
            classification = {"domain": "software", "capability": "coding", "confidence": 0.9}

            def invoke(arguments):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = muse_dev.main(arguments)
                return code, json.loads(output.getvalue())

            with patch.object(muse_dev.chatgpt_bridge, "classify_request", return_value=classification):
                common = ["commander", "submit", "--request", request, "--request-id", request_id, "--database", database]
                code1, first = invoke(common)
                code2, second = invoke(common)
            self.assertEqual((code1, code2), (0, 0))
            self.assertEqual(first["task_id"], second["task_id"])
            self.assertEqual(first["status"], "waiting_approval")
            task_id = first["task_id"]

            code, status = invoke(["commander", "status", "--task-id", task_id, "--database", database])
            self.assertEqual(code, 0)
            self.assertEqual(status["ticket"]["status"], "waiting_approval")
            self.assertEqual(status["execution"], "not_started")
            self.assertNotIn("payload", status["ticket"])

            code, approved = invoke(["commander", "approve", "--task-id", task_id, "--approved-by", "user", "--database", database])
            self.assertEqual(code, 0)
            self.assertEqual(approved["status"], "queued")
            self.assertEqual(approved["execution"], "not_started")

            code, listing = invoke(["commander", "list", "--database", database, "--limit", "10"])
            self.assertEqual(code, 0)
            self.assertEqual(listing["counts"]["queued"], 1)
            with TaskQueue(database) as queue:
                task = queue.get(task_id)
                self.assertEqual(task["status"], "queued")
                self.assertIn("human.decision", [event["event_type"] for event in queue.events(task_id)])


if __name__ == "__main__":
    unittest.main()
