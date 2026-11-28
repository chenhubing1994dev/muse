"""Muse Commander — the single control plane over the single task store of record.

This module does not introduce a second queue. It composes existing primitives:

* :class:`muse.task_queue.TaskQueue`            — SSOT for tickets, DAG deps, approvals, leases
* :class:`muse.autonomy_safety.SafetyController` — pause / kill switch / breaker / backpressure
* :func:`muse.consciousness_loop.assess_risk`    — risk tiering of every step
* :class:`muse.review_gate.IndependentReviewGate` — independent review (builder ≠ reviewer)
* :class:`muse.usage_ledger.UsageLedger`         — every model/tool call costed locally
* :mod:`muse.resource_governor`                  — observed-host concurrency ceiling
* :mod:`muse.adapter_hub`                        — external frameworks only via Commander grants

Workflow B: goal → Task DAG → risk tier → (high/medium) human approval gate → dispatch with
timeout + cooperative cancel → independent objective review (bounded retries) → hash-chained
completion receipt.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from muse.adapter_hub import AdapterHub
from muse.autonomy_safety import SafetyController
from muse.consciousness_loop import assess_risk
from muse.error_journal import record_suppressed_error
from muse.resource_governor import ResourcePolicy, probe_host_resources
from muse.review_gate import IndependentReviewGate, ReviewStore
from muse.task_queue import TaskQueue
from muse.usage_ledger import UsageLedger

HUMAN_PREFIXES = ("user:", "human:")
ROLE_WORKLOAD = {"research": "research", "researcher": "research", "developer": "coding", "programmer": "coding",
                 "operator": "operations", "writer": "writing", "video": "media", "media": "media"}
Handler = Callable[[dict[str, Any]], dict[str, Any]]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def objective_reviewer(request_json: str) -> str:
    """Reviewer that trusts nothing the builder says: it re-derives the evidence itself.

    Lineage must embed ``artifact_sha256=<hex>``; the reviewer recomputes the hash of the
    artifact it received, requires non-trivial content and a PASS marker in test evidence
    with zero FAIL markers. It lives in its own function boundary and never calls the builder.
    """
    request = json.loads(request_json)
    artifact, lineage = str(request.get("artifact", "")), str(request.get("lineage", ""))
    evidence = str(request.get("test_evidence", ""))
    findings = []
    claimed = ""
    for part in lineage.split(";"):
        if part.strip().startswith("artifact_sha256="):
            claimed = part.split("=", 1)[1].strip()
    if claimed != sha256_text(artifact):
        findings.append({"severity": "critical", "title": "hash mismatch", "detail": "artifact hash does not match lineage"})
    if len(artifact.strip()) < 8:
        findings.append({"severity": "high", "title": "empty artifact", "detail": "artifact shorter than 8 characters"})
    if "PASS" not in evidence or "FAIL" in evidence:
        findings.append({"severity": "high", "title": "no passing evidence", "detail": "test evidence lacks PASS or contains FAIL"})
    approved = not findings
    return json.dumps({"approved": approved, "summary": "objective checks passed" if approved else "objective checks failed",
                       "findings": findings}, ensure_ascii=False)


class CommanderError(RuntimeError):
    pass


class MuseCommander:
    def __init__(self, root: str | Path, *, reviewer_id: str = "reviewer:objective",
                 reviewer_call: Callable[[str], str] = objective_reviewer,
                 max_review_retries: int = 1) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.queue = TaskQueue(self.root / "tasks.sqlite3")
        self.safety = SafetyController(self.root / "safety.sqlite3", kill_switch_file=self.root / "KILL_SWITCH")
        self.review_store = ReviewStore(self.root / "reviews.sqlite3")
        self.reviewer = IndependentReviewGate(self.review_store, reviewer_id=reviewer_id, call=reviewer_call,
                                              task_kind="commander_step_review")
        self.usage = UsageLedger(self.root / "usage.sqlite3")
        self.receipts = self.root / "receipts.jsonl"
        self.handlers: dict[str, Handler] = {}
        self.max_review_retries = max(0, int(max_review_retries))
        secret_file = self.root / "grant.secret"
        if not secret_file.exists():
            secret_file.write_text(secrets.token_hex(32), encoding="utf-8")
        self.adapters = AdapterHub(bytes.fromhex(secret_file.read_text(encoding="utf-8").strip()))
        self._cancel_events: dict[str, threading.Event] = {}

    def close(self) -> None:
        for closer in (self.queue.close, self.safety.close, self.review_store.close, self.usage.close):
            try:
                closer()
            except Exception as exc:
                record_suppressed_error("muse_commander.close", exc)

    def __enter__(self) -> "MuseCommander":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- capacity -------------------------------------------------------------------
    @staticmethod
    def capacity() -> dict[str, Any]:
        snapshot = probe_host_resources()
        decision = ResourcePolicy().decide(snapshot)
        info = decision.as_dict() if hasattr(decision, "as_dict") else {"decision": str(decision)}
        return {"snapshot": {"logical_cpu_count": snapshot.logical_cpu_count,
                             "available_memory_bytes": snapshot.available_memory_bytes,
                             "total_memory_bytes": snapshot.total_memory_bytes,
                             "gpu_available_memory_bytes": snapshot.gpu_available_memory_bytes
                             if snapshot.gpu_available_memory_bytes is not None else "unknown"},
                "decision": info}

    # ---- planning -------------------------------------------------------------------
    def register_handler(self, name: str, handler: Handler) -> None:
        if not callable(handler):
            raise ValueError("handler must be callable")
        self.handlers[name] = handler

    def submit_goal(self, goal: str, steps: list[dict[str, Any]], *, plan_id: str | None = None) -> dict[str, Any]:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be non-empty")
        if not steps:
            raise ValueError("a plan needs at least one step")
        plan_id = plan_id or uuid.uuid4().hex[:16]
        ids: dict[str, str] = {}
        tickets = []
        for step in steps:
            key = str(step["key"])
            role = str(step.get("role", "researcher"))
            objective = str(step.get("objective", step.get("title", key)))
            risk, reasons = assess_risk(role, objective)
            if step.get("risk_override") in ("high", "medium") and risk == "low":
                risk, reasons = step["risk_override"], [*reasons, "declared_by_planner"]
            deps = tuple(ids[d] for d in step.get("depends_on", ()))
            ticket = self._enqueue_idempotent(
                str(step.get("title", key)),
                {"kind": "commander_step", "plan_id": plan_id, "goal": goal, "step_key": key, "role": role,
                 "objective": objective, "handler": str(step.get("handler", "")), "risk": risk,
                 "risk_reasons": reasons, "adapter": step.get("adapter"),
                 **({"route_args": step["route_args"]} if isinstance(step.get("route_args"), dict) else {})},
                depends_on=deps, require_approval=risk in ("high", "medium"),
                # High-risk work must not silently resume after a crash: expired leases go back to a human.
                recovery_policy="manual" if risk == "high" else "retry",
                idempotency_key=f"commander:{plan_id}:{key}",
                workload_class=ROLE_WORKLOAD.get(role, "general"), estimated_seconds=60,
            )
            ids[key] = ticket["id"]
            tickets.append({"key": key, "task_id": ticket["id"], "status": ticket["status"], "risk": risk})
        return {"plan_id": plan_id, "goal": goal, "tickets": tickets}

    def _enqueue_idempotent(self, title: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Concurrent submissions of the same plan step race on the UNIQUE idempotency key: one wins,
        the other returns the winner's ticket instead of raising."""
        import sqlite3
        try:
            return self.queue.enqueue(title, payload, **kwargs)
        except sqlite3.IntegrityError:
            existing = self.queue.get_by_idempotency_key(kwargs["idempotency_key"])
            if existing is None:
                raise
            return existing

    def _receipt_for(self, task_id: str) -> dict[str, Any] | None:
        if not self.receipts.exists():
            return None
        found = None
        for line in self.receipts.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                body = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CommanderError("receipt chain contains a corrupt line") from exc
            if body.get("task_id") == task_id:
                found = body
        return found

    # ---- approval gate --------------------------------------------------------------
    def approve(self, task_id: str, *, approved_by: str) -> dict[str, Any]:
        if not isinstance(approved_by, str) or not approved_by.startswith(HUMAN_PREFIXES):
            raise PermissionError("high/medium-risk steps require a human approver (user:/human:)")
        return self.queue.approve(task_id, approved_by=approved_by)

    def cancel(self, task_id: str, *, by: str, reason: str = "") -> dict[str, Any]:
        event = self._cancel_events.get(task_id)
        if event is not None:
            event.set()
            return {"task_id": task_id, "status": "cancel_signalled"}
        return self.queue.cancel(task_id, cancelled_by=by, reason=reason)

    # ---- full-table reads (TaskQueue.list_tasks is paged to 200 by design) ------------
    def _task_rows(self, statuses: tuple[str, ...] | None = None) -> list[tuple[str, str]]:
        import sqlite3
        con = sqlite3.connect(self.root / "tasks.sqlite3", timeout=10)
        try:
            if statuses:
                marks = ",".join("?" for _ in statuses)
                rows = con.execute(f"SELECT id, status FROM tasks WHERE status IN ({marks}) ORDER BY created_at, id",
                                   statuses).fetchall()
            else:
                rows = con.execute("SELECT id, status FROM tasks ORDER BY created_at, id").fetchall()
        finally:
            con.close()
        return [(str(r[0]), str(r[1])) for r in rows]

    def kill(self, *, actor: str, reason: str) -> dict[str, Any]:
        """Emergency stop: block dispatch, signal running steps, cancel every unstarted ticket."""
        status = self.safety.engage_kill_switch(actor=actor, reason=reason)
        # The kill file stops every Muse dispatch path (Ruflo DAG, autonomous runtime), not only this facade.
        from muse.commander_gate import KILL_FILE_NAME
        (self.root / KILL_FILE_NAME).write_text(f"actor={actor}\nreason={reason}\n", encoding="utf-8")
        for event in self._cancel_events.values():
            event.set()
        cancelled = []
        for task_id, _status in self._task_rows(("queued", "waiting_approval")):
            try:
                self.queue.cancel(task_id, cancelled_by=actor, reason=f"kill switch: {reason}"[:500])
                cancelled.append(task_id)
            except (ValueError, RuntimeError) as exc:
                record_suppressed_error("muse_commander.kill.cancel", exc, task_id=task_id)
        return {"safety": status, "cancelled": cancelled}

    # ---- dispatch -------------------------------------------------------------------
    def _run_handler(self, handler: Handler, ctx: dict[str, Any], timeout: float) -> dict[str, Any]:
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["result"] = handler(ctx)
            except BaseException as exc:  # reported as structured failure below
                box["error"] = exc

        thread = threading.Thread(target=target, name=f"commander-{ctx['task_id'][:8]}", daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            ctx["cancel_event"].set()
            return {"_failure": "timeout", "detail": f"handler exceeded {timeout}s; cancel signalled"}
        if "error" in box:
            exc = box["error"]
            return {"_failure": "handler_exception", "detail": f"{type(exc).__name__}: {exc}"[:500]}
        result = box.get("result")
        if not isinstance(result, dict):
            return {"_failure": "invalid_result", "detail": "handler must return a dict"}
        return result

    def run_next(self, *, worker_id: str = "commander-worker", timeout_seconds: float = 30.0,
                 task_id: str | None = None) -> dict[str, Any]:
        gate = self.safety.check()
        if not gate.get("allowed"):
            return {"status": "blocked", "gate": gate}
        if self.root.name == "commander" and self.root.parent.name == "data":
            # Also obey the Omega dashboard switch of the same workspace (one stop button for everything).
            from muse.commander_gate import _omega_gate
            omega = _omega_gate(self.root.parent.parent)
            if omega is not None:
                return {"status": "blocked", "gate": omega}
        task = self.queue.claim_next(worker_id=worker_id, lease_seconds=max(30, int(timeout_seconds) + 30),
                                     task_id=task_id)
        if task is None:
            return {"status": "idle"}
        task_id = task["id"]
        payload = task.get("payload") or {}
        try:
            prior = self._receipt_for(task_id)
        except CommanderError as exc:  # a corrupt receipt chain fails closed instead of crashing the worker
            result = {"error_code": "commander.receipt_chain_corrupt", "failure_category": "storage",
                      "retryable": False, "detail": str(exc)[:500]}
            self.queue.finish(task_id, status="failed", result=result, worker_id=worker_id)
            self.safety.record_result(False, error="receipt_chain_corrupt")
            record = {"status": "failed", "task_id": task_id, "result": result}
            self._persist_result(task_id, record)
            return record
        if prior is not None:
            # Crash after the receipt was written but before the ticket was finished: the work is done.
            # Never re-run the handler (no duplicate side effects, receipts or costs).
            result = {"artifact_sha256": prior["artifact_sha256"], "review_id": prior["review_id"],
                      "receipt_sha256": prior["receipt_sha256"], "recovered_from_receipt": True}
            stored = self.stored_result(task_id) or {}
            if isinstance(stored.get("result"), dict):
                result = {**stored["result"], "recovered_from_receipt": True}
            self.queue.finish(task_id, status="succeeded", result=result, worker_id=worker_id)
            record = {"status": "succeeded", "task_id": task_id, "result": result}
            self._persist_result(task_id, record)
            return record
        handler = self.handlers.get(str(payload.get("handler", "")))
        if handler is None:
            result = {"error_code": "commander.handler_missing", "failure_category": "configuration",
                      "retryable": False, "detail": f"no handler {payload.get('handler')!r}"}
            self.queue.finish(task_id, status="failed", result=result, worker_id=worker_id)
            self.safety.record_result(False, error="handler_missing")
            return {"status": "failed", "task_id": task_id, "result": result}
        cancel_event = threading.Event()
        self._cancel_events[task_id] = cancel_event
        try:
            outcome_record = self._execute(task_id, payload, handler, cancel_event, worker_id, timeout_seconds)
            self._persist_result(task_id, outcome_record)
            return outcome_record
        finally:
            self._cancel_events.pop(task_id, None)

    def _persist_result(self, task_id: str, record: dict[str, Any]) -> None:
        """Disk is the source of truth for ticket outcomes (TaskQueue stores status, not results)."""
        folder = self.root / "results"
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{task_id}.json.tmp"
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(folder / f"{task_id}.json")

    def stored_result(self, task_id: str) -> dict[str, Any] | None:
        path = self.root / "results" / f"{task_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def drain_plan(self, task_ids: list[str], *, timeout_seconds: float = 30.0) -> list[dict[str, Any]]:
        """Run only this plan's queued tickets in order; never touches other plans' tickets."""
        outcomes = []
        progressed = True
        while progressed:
            progressed = False
            for tid in task_ids:
                task = self.queue.get(tid)
                if task is None or task["status"] != "queued":
                    continue
                outcome = self.run_next(task_id=tid, timeout_seconds=timeout_seconds)
                outcomes.append(outcome)
                if outcome["status"] == "blocked":
                    return outcomes
                if outcome["status"] != "idle":
                    progressed = True
        return outcomes

    def _execute(self, task_id: str, payload: dict[str, Any], handler: Handler, cancel_event: threading.Event,
                 worker_id: str, timeout_seconds: float) -> dict[str, Any]:
        if True:  # body kept at the original indentation of the pre-refactor try-block
            attempts = []
            for attempt in range(self.max_review_retries + 1):
                ctx = {"task_id": task_id, "payload": payload, "attempt": attempt, "cancel_event": cancel_event,
                       "grant": (self.adapters.issue_grant(adapter=str(payload["adapter"]), task_id=task_id)
                                 if payload.get("adapter") else None),
                       "adapters": self.adapters, "commander_root": str(self.root), "worker_id": worker_id}
                outcome = self._run_handler(handler, ctx, timeout_seconds)
                if "_failure" in outcome or cancel_event.is_set():
                    reason = outcome.get("_failure", "cancelled")
                    status = "cancelled" if reason in ("cancelled",) or (cancel_event.is_set() and reason != "timeout") else "failed"
                    result = {"error_code": f"commander.{reason}", "failure_category": "execution",
                              "retryable": reason == "timeout", "detail": outcome.get("detail", "cancelled")}
                    self.queue.finish(task_id, status=status, result=result, worker_id=worker_id)
                    self.safety.record_result(False, error=reason)
                    return {"status": status, "task_id": task_id, "result": result}
                self._record_usage(task_id, outcome.get("usage"), payload)
                artifact = str(outcome.get("artifact", ""))
                builder_id = str(outcome.get("builder_id") or f"worker:{payload.get('role', 'unknown')}")
                review = self.reviewer.review(
                    artifact_id=f"{task_id}:{attempt}", builder_id=builder_id, artifact=artifact or "(empty)",
                    test_evidence=str(outcome.get("evidence") or "(none)"),
                    lineage=f"task={task_id};artifact_sha256={sha256_text(artifact)}")
                attempts.append({"attempt": attempt, "review_id": review.review_id, "approved": review.approved})
                if review.approved:
                    receipt = self._receipt(task_id, payload, artifact, review, builder_id)
                    result = {"artifact_sha256": sha256_text(artifact), "review_id": review.review_id,
                              "receipt_sha256": receipt["receipt_sha256"], "attempts": attempts,
                              "files": outcome.get("files", []), "output": outcome.get("output")}
                    self.queue.finish(task_id, status="succeeded", result=result, worker_id=worker_id)
                    self.safety.record_result(True)
                    return {"status": "succeeded", "task_id": task_id, "result": result}
            result = {"error_code": "commander.review_rejected", "failure_category": "review", "retryable": False,
                      "attempts": attempts}
            self.queue.finish(task_id, status="failed", result=result, worker_id=worker_id)
            self.safety.record_result(False, error="review_rejected")
            return {"status": "failed", "task_id": task_id, "result": result}

    def drain(self, *, max_steps: int = 50, timeout_seconds: float = 30.0) -> list[dict[str, Any]]:
        outcomes = []
        for _ in range(max_steps):
            outcome = self.run_next(timeout_seconds=timeout_seconds)
            if outcome["status"] in ("idle", "blocked"):
                outcomes.append(outcome)
                break
            outcomes.append(outcome)
        return outcomes

    # ---- ledgers --------------------------------------------------------------------
    def _record_usage(self, task_id: str, usage: Any, payload: dict[str, Any]) -> None:
        if not isinstance(usage, dict):
            return
        self.usage.record_usage(
            str(usage.get("provider", "local")), str(usage.get("model", "unknown")),
            token_source=str(usage.get("token_source", "estimated")),
            input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
            cost_amount=str(usage.get("cost", "0")), cost_currency="USD", cost_kind="estimated",
            cost_source="muse_commander", price_version="local-v1", task_id=task_id,
            agent_id=str(payload.get("role", "unknown")), project_id=str(payload.get("plan_id", "general")))
        # Cost chain: every usage row is mirrored by a hash-chained digest (muse.audit verify cross-checks).
        from muse.audit import append_chained, usage_row_digest
        known = set()
        chain = self.root / "cost_chain.jsonl"
        if chain.is_file():
            known = {json.loads(line).get("event_id") for line in chain.read_text(encoding="utf-8").splitlines() if line.strip()}
        for row in self.usage.list_events(task_id=task_id, limit=500):
            if row["event_id"] not in known:
                append_chained(chain, {"event_id": row["event_id"], "task_id": task_id,
                                       "row_sha256": usage_row_digest(row)})

    def _receipt(self, task_id: str, payload: dict[str, Any], artifact: str, review: Any, builder_id: str) -> dict[str, Any]:
        previous = ""
        if self.receipts.exists():
            lines = self.receipts.read_text(encoding="utf-8").splitlines()
            if lines:
                try:
                    previous = json.loads(lines[-1]).get("receipt_sha256", "")
                except json.JSONDecodeError as exc:
                    raise CommanderError("receipt chain tail is corrupt; refusing to extend it") from exc
        body = {"task_id": task_id, "plan_id": payload.get("plan_id"), "step_key": payload.get("step_key"),
                "artifact_sha256": sha256_text(artifact), "builder_id": builder_id,
                "reviewer_id": review.reviewer_id, "review_id": review.review_id,
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "prev_receipt_sha256": previous}
        body["receipt_sha256"] = sha256_text(json.dumps(body, sort_keys=True, ensure_ascii=False))
        with self.receipts.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(body, ensure_ascii=False) + "\n")
        return body

    def verify_receipt_chain(self) -> dict[str, Any]:
        if not self.receipts.exists():
            return {"ok": True, "count": 0}
        previous = ""
        count = 0
        for line in self.receipts.read_text(encoding="utf-8").splitlines():
            body = json.loads(line)
            claimed = body.pop("receipt_sha256")
            if body.get("prev_receipt_sha256") != previous or sha256_text(json.dumps(body, sort_keys=True, ensure_ascii=False)) != claimed:
                return {"ok": False, "broken_at": count}
            previous, count = claimed, count + 1
        return {"ok": True, "count": count}

    def status(self) -> dict[str, Any]:
        by: dict[str, int] = {}
        for _task_id, task_status in self._task_rows():
            by[task_status] = by.get(task_status, 0) + 1
        return {"tasks": by, "safety": self.safety.status(), "receipts": self.verify_receipt_chain()}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Muse Commander control plane (status / kill / resume)")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2] / "data" / "commander"))
    parser.add_argument("command", choices=("status", "kill", "reset-kill", "capacity"))
    parser.add_argument("--actor", default="user:cli")
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args(argv)
    with MuseCommander(args.root) as commander:
        if args.command == "status":
            output: Any = commander.status()
        elif args.command == "kill":
            output = commander.kill(actor=args.actor, reason=args.reason)
        elif args.command == "reset-kill":
            from muse.autonomy_safety import KILL_RESET_PHRASE
            # SafetyController refuses to reset while the kill file exists: remove it first, then reset.
            kill_file = commander.root / "KILL_SWITCH"
            if kill_file.exists():
                kill_file.unlink()
            # Two-step release by design: reset leaves the controller 'paused'; an explicit resume reopens dispatch.
            commander.safety.reset_kill_switch(actor=args.actor, confirmation=KILL_RESET_PHRASE)
            output = commander.safety.resume(actor=args.actor)
        else:
            output = commander.capacity()
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
