"""Commander-routed write endpoints for inspiration and research (T2).

Public HTTP write paths that used to act directly now create a Muse Commander ticket and wait
(default 30 s) for it inside the server process:

* ``POST /api/v1/inspirations``               → handler ``route.inspiration_capture`` (low risk)
* ``POST /api/v1/inspirations/evolve``        → handler ``route.inspiration_evolve``; ``enqueue_video`` /
  ``create_offline_product`` spend GPU or create products ⇒ medium risk ⇒ ``pending_approval``
* ``POST /api/v1/control/xuanshu/hypotheses`` with ``write_to_wiki`` → ``route.xuanshu_write``

The real work lives in the ``*_core`` functions below. Each core refuses to run unless it is
called from inside a Commander ticket that is currently ``running`` with the matching handler
(:class:`BypassCommanderError`), and every refusal is written to an append-only audit log.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from muse.error_journal import record_suppressed_error

DEFAULT_WAIT_SECONDS = 30.0
HANDLER_CAPTURE = "route.inspiration_capture"
HANDLER_EVOLVE = "route.inspiration_evolve"
HANDLER_XUANSHU = "route.xuanshu_write"
HANDLER_DAEMON = "route.daemon_control"


from muse.commander_gate import BypassCommanderError  # single shared exception type (T2 + T6)


def commander_root_for(workspace_root: str | Path) -> Path:
    from muse.commander_gate import commander_root
    return commander_root(workspace_root)


def _audit_bypass(root: Path, *, function: str, reason: str, ctx: Any) -> None:
    root.mkdir(parents=True, exist_ok=True)
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "function": function, "reason": reason,
             "claimed_task_id": str((ctx or {}).get("task_id", ""))[:64] if isinstance(ctx, dict) else ""}
    with (root / "bypass_audit.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def require_commander_ticket(ctx: Any, *, handler: str, function: str, workspace_root: str | Path) -> None:
    """Fail closed unless ``ctx`` is the live context of a running Commander ticket for ``handler``."""
    root = commander_root_for(workspace_root)
    reason = ""
    if not isinstance(ctx, dict) or not ctx.get("task_id") or not ctx.get("commander_root"):
        reason = "no commander ticket context"
    elif Path(str(ctx["commander_root"])).resolve() != root:
        reason = "ticket belongs to a different commander root"
    else:
        db = root / "tasks.sqlite3"
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
            try:
                row = con.execute("SELECT status, payload_json, lease_owner FROM tasks WHERE id=?",
                                  (str(ctx["task_id"]),)).fetchone()
            finally:
                con.close()
        except sqlite3.Error as exc:
            row, reason = None, f"commander store unreadable: {type(exc).__name__}"
        if not reason:
            if row is None:
                reason = "ticket not found"
            elif row[0] != "running":
                reason = f"ticket is {row[0]}, not running"
            elif json.loads(row[1] or "{}").get("handler") != handler:
                reason = "ticket handler mismatch"
            elif ctx.get("worker_id") and row[2] != ctx.get("worker_id"):
                reason = "ticket lease held by another worker"
    if reason:
        _audit_bypass(root, function=function, reason=reason, ctx=ctx)
        record_suppressed_error(f"commander_routes.bypass:{function}", BypassCommanderError(reason))
        raise BypassCommanderError(f"{function} must run inside a Muse Commander ticket ({reason})")


# ---------------------------------------------------------------------------------------- cores
def capture_inspiration_core(workspace_root: str | Path, knowledge_path: str | Path, fields: dict[str, Any],
                             *, commander_ctx: Any) -> dict[str, Any]:
    require_commander_ticket(commander_ctx, handler=HANDLER_CAPTURE, function="capture_inspiration_core",
                             workspace_root=workspace_root)
    from muse.research_knowledge import ResearchKnowledgeStore
    content = fields["content"]
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with ResearchKnowledgeStore(Path(knowledge_path)) as knowledge:
        node = knowledge.upsert_node(
            "claim", fields["title"], summary=content,
            external_ref="creator-inspiration:sha256:" + content_sha256,
            metadata={
                "origin": "creator_inspiration_inbox",
                "epistemic_status": "creator_inspired_hypothesis_only",
                "creator_context_is_evidence": False,
                "requires_support_and_counter_evidence": True,
                "review_state": "not_researched",
                "allow_external_research": fields["allow_external_research"],
                "submitted_via": "muse_local_dashboard",
                "source_kind": fields["source"],
                "content_sha256": content_sha256,
                "tags": fields["tags"],
                "research_domains": fields["research_domains"],
                "commander_task_id": str(commander_ctx["task_id"]),
            },
            provenance=(fields["source"],),
            status="proposed",
        )
    return {"id": node["id"], "title": node["title"], "content": node["summary"],
            "created_at": node["created_at"],
            "status": node.get("metadata", {}).get("review_state", "not_researched"),
            "allow_external_research": fields["allow_external_research"],
            "tags": fields["tags"], "research_domains": fields["research_domains"], "source": fields["source"]}


def enqueue_evolution_core(workspace_root: str | Path, task_path: str | Path, task_payload: dict[str, Any],
                           *, commander_ctx: Any) -> dict[str, Any]:
    """Hand the approved evolution to the runtime worker queue (the only allowed enqueue point)."""
    require_commander_ticket(commander_ctx, handler=HANDLER_EVOLVE, function="enqueue_evolution_core",
                             workspace_root=workspace_root)
    from muse.task_queue import TaskQueue
    idempotency_key = "inspiration-evolution:" + task_payload["request_id"]
    with TaskQueue(Path(task_path)) as queue:
        existing = queue.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            existing_payload = existing.get("payload")
            existing_request = ({key: existing_payload.get(key, False) if key == "create_offline_product" else existing_payload.get(key)
                                 for key in task_payload} if isinstance(existing_payload, dict) else None)
            if existing_request != task_payload:
                raise ValueError("request_id was already used for a different inspiration evolution")
        task = queue.enqueue("Evolve creator inspiration", dict(task_payload), priority=65, workload_class="research",
                             recovery_policy="retry", idempotency_key=idempotency_key)
    return {"runtime_task_id": task["id"], "runtime_status": task["status"], "deduplicated": existing is not None}


def write_xuanshu_hypotheses_core(workspace_root: str | Path, knowledge_path: str | Path, hypotheses: dict[str, Any],
                                  *, commander_ctx: Any) -> dict[str, Any]:
    require_commander_ticket(commander_ctx, handler=HANDLER_XUANSHU, function="write_xuanshu_hypotheses_core",
                             workspace_root=workspace_root)
    from muse.metaphysics_label import write_hypotheses_to_wiki
    from muse.research_knowledge import ResearchKnowledgeStore
    Path(knowledge_path).parent.mkdir(parents=True, exist_ok=True)
    with ResearchKnowledgeStore(Path(knowledge_path)) as store:
        return write_hypotheses_to_wiki(store, hypotheses)


# ------------------------------------------------------------------------------------ handlers
def _result(output: dict[str, Any], *, role: str, checks: list[str]) -> dict[str, Any]:
    return {"artifact": json.dumps(output, ensure_ascii=False, sort_keys=True, default=str),
            "builder_id": f"worker:{role}", "evidence": "; ".join(f"{c}: PASS" for c in checks),
            "output": output,
            "usage": {"provider": "local_deterministic", "model": "none", "token_source": "not_reported"}}


def build_route_handlers(workspace_root: str | Path) -> dict[str, Any]:
    ws = Path(workspace_root).resolve()

    def capture(ctx: dict[str, Any]) -> dict[str, Any]:
        args = ctx["payload"]["route_args"]
        out = capture_inspiration_core(ws, args["knowledge_path"], args["fields"], commander_ctx=ctx)
        return _result(out, role="researcher", checks=["inspiration_persisted", "labelled_not_evidence"])

    def evolve(ctx: dict[str, Any]) -> dict[str, Any]:
        args = ctx["payload"]["route_args"]
        out = enqueue_evolution_core(ws, args["task_path"], args["task_payload"], commander_ctx=ctx)
        return _result(out, role="researcher", checks=["runtime_handoff_recorded"])

    def xuanshu(ctx: dict[str, Any]) -> dict[str, Any]:
        args = ctx["payload"]["route_args"]
        out = write_xuanshu_hypotheses_core(ws, args["knowledge_path"], args["hypotheses"], commander_ctx=ctx)
        return _result(out, role="researcher", checks=["hypotheses_written_as_proposed", "symbolic_label_locked"])

    def external_invoke(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.external_agents import ExternalAgentAdapter
        args = ctx["payload"]["route_args"]
        adapter = ExternalAgentAdapter(str(args["adapter"]), config_path=args.get("config_path"))
        out = adapter.invoke(args["request"], commander_ctx=ctx, workspace=ws)
        return _result(out, role="operator", checks=["human_approved", "egress_allowlist", "loopback_only"])

    def daemon_control(ctx: dict[str, Any]) -> dict[str, Any]:
        require_commander_ticket(ctx, handler=HANDLER_DAEMON, function="daemon_control", workspace_root=ws)
        from muse.daemon_control import MuseDaemonController
        args = ctx["payload"]["route_args"]
        action = str(args["action"])
        if action not in {"start", "resume", "once"}:
            raise ValueError(f"unsupported daemon action {action!r}")
        daemon = MuseDaemonController(workspace=args["workspace"], state_db=args["state_db"], control_log=args["control_log"])
        if action == "start":
            out = daemon.start()
        elif action == "resume":
            out = daemon.resume()
        else:
            out = {"changed": True, "action": "once", **daemon.run_once()}
        return _result(out, role="operator", checks=["global_gate_open", "commander_ticket"])

    return {HANDLER_CAPTURE: capture, HANDLER_EVOLVE: evolve, HANDLER_XUANSHU: xuanshu, HANDLER_DAEMON: daemon_control,
            "route.external_invoke": external_invoke}


# ------------------------------------------------------------------------------- submit & wait
def submit_and_wait(workspace_root: str | Path, *, handler: str, route_args: dict[str, Any], title: str,
                    objective: str, request_key: str, risk_override: str | None = None,
                    wait_seconds: float = DEFAULT_WAIT_SECONDS) -> dict[str, Any]:
    """Create (idempotently) one Commander ticket and run it in-process up to ``wait_seconds``.

    Returns ``{"ticket_id", "status", "output", "receipt_sha256", ...}`` where status is
    ``succeeded`` | ``failed`` | ``pending_approval`` | ``queued`` (global gate closed / timeout) | ``cancelled``.
    """
    from muse.muse_commander import MuseCommander
    ws = Path(workspace_root).resolve()
    # The ticket identity includes the target stores (route_args): the same request aimed at a different
    # workspace/store is a different ticket and must never replay another store's output.
    args_digest = hashlib.sha256(json.dumps(route_args, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    plan_id = hashlib.sha256(f"{handler}|{request_key}|{args_digest}".encode("utf-8")).hexdigest()[:16]
    step = {"key": "route", "title": title, "role": "researcher", "objective": objective, "handler": handler,
            "route_args": route_args}
    if risk_override:
        step["risk_override"] = risk_override
    with MuseCommander(commander_root_for(ws)) as commander:
        for name, fn in build_route_handlers(ws).items():
            commander.register_handler(name, fn)
        plan = _submit_with_args(commander, objective, step, plan_id)
        ticket_id = plan["tickets"][0]["task_id"]
        task = commander.queue.get(ticket_id)
        replayed = task["status"] in {"succeeded", "failed", "cancelled"}
        outcome: dict[str, Any] | None = None
        if task["status"] == "queued":
            outcome = commander.run_next(task_id=ticket_id, timeout_seconds=wait_seconds)
            task = commander.queue.get(ticket_id)
        stored = commander.stored_result(ticket_id) or {}
    status = task["status"]
    if status == "waiting_approval":
        status = "pending_approval"
    result = stored.get("result") or {}
    return {"ticket_id": ticket_id, "plan_id": plan_id, "status": status, "risk": plan["tickets"][0]["risk"],
            "replayed": replayed,
            "output": result.get("output"), "receipt_sha256": result.get("receipt_sha256"),
            "error_code": result.get("error_code"), "detail": result.get("detail"),
            "gate": (outcome or {}).get("gate") if (outcome or {}).get("status") == "blocked" else None}


def _submit_with_args(commander: Any, goal: str, step: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """submit_goal() keeps only known keys in the payload; route_args must travel with the ticket."""
    plan = commander.submit_goal(goal, [step], plan_id=plan_id)
    ticket_id = plan["tickets"][0]["task_id"]
    task = commander.queue.get(ticket_id)
    if "route_args" not in (task.get("payload") or {}):
        raise RuntimeError("commander ticket payload lost route_args")
    return plan


def audit_safety_action(workspace_root: str | Path, *, action: str, outcome: str) -> None:
    """Append-only audit line for safety-down actions that intentionally bypass ticket execution."""
    root = commander_root_for(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": str(action)[:80], "outcome": str(outcome)[:300]}
    with (root / "safety_actions.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
