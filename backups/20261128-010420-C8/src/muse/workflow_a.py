"""Workflow A — 求真与创造主干, executed step-by-step as Muse Commander tickets.

inspire  → symbolic lab (Xuanshu fusion, hard-locked labels) → testable hypotheses → Research Wiki claims
research → per claim: experiment design + alternative explanation + honest verdict (no sources ⇒ insufficient_evidence)
memory   → Agent Track experience (never User Track) + optional create-only Obsidian export
build    → InitiativeStore initiative → StaticPrototypeBuilder static prototype → structural validation
evolve   → evolution *proposal* draft only (risk=high ⇒ waits for a human; never applies a patch)

Every step is a Commander ticket: risk tiering, approval gate, timeout, cancel, independent objective
review, cost ledger and hash-chained receipt all come from :class:`muse.muse_commander.MuseCommander`.
Steps hand state over through ``<run_dir>/<step>.json`` so the disk state is the source of truth.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from muse.muse_commander import MuseCommander

STEPS = ("inspire", "research", "memory", "build", "evolve")


def _write_state(run_dir: Path, step: str, data: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"{step}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _read_state(run_dir: Path, step: str) -> dict[str, Any]:
    path = run_dir / f"{step}.json"
    if not path.is_file():
        raise FileNotFoundError(f"upstream step state missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _result(artifact_obj: Any, *, role: str, checks: list[str], files: list[Path]) -> dict[str, Any]:
    artifact = json.dumps(artifact_obj, ensure_ascii=False, sort_keys=True)
    return {"artifact": artifact, "builder_id": f"worker:{role}",
            "evidence": "; ".join(f"{c}: PASS" for c in checks),
            "files": [str(f) for f in files],
            "usage": {"provider": "local_deterministic", "model": "none", "token_source": "not_reported"}}


def build_handlers(workspace: Path, *, obsidian_vault: Path | None = None) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    data = workspace / "data"

    def run_dir(ctx: dict[str, Any]) -> Path:
        return data / "workflow_a" / str(ctx["payload"]["plan_id"])

    def inspire(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.metaphysics_label import symbolic_to_hypotheses, write_hypotheses_to_wiki
        from muse.research_wiki import ResearchWiki
        from muse.xuanshu_fusion import fuse, validate_labels
        question = str(ctx["payload"]["goal"])
        fused = fuse(question, seed=f"wf-a:{ctx['payload']['plan_id']}", symbolic_fallback=True)
        validate_labels(fused)
        hypotheses = symbolic_to_hypotheses(fused)
        with ResearchWiki(data / "research" / "knowledge.sqlite3") as wiki:
            written = write_hypotheses_to_wiki(wiki.knowledge, hypotheses)
        state = {"question": question, "idea_id": written["idea_id"],
                 "claim_ids": [c["id"] for c in written["claims"]],
                 "epistemic_tag": "symbolic_inspiration", "is_evidence": False}
        if not state["claim_ids"]:
            raise RuntimeError("symbolic lab produced no testable hypotheses")
        path = _write_state(run_dir(ctx), "inspire", state)
        return _result(state, role="researcher", checks=["xuanshu_labels", "hypotheses_written"], files=[path])

    def research(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.research_wiki import ResearchWiki
        upstream = _read_state(run_dir(ctx), "inspire")
        verdicts = {}
        with ResearchWiki(data / "research" / "knowledge.sqlite3") as wiki:
            for claim_id in upstream["claim_ids"]:
                claim = wiki.knowledge.get_node(claim_id)
                if claim is None:
                    raise LookupError(f"claim vanished: {claim_id}")
                wiki.add_experiment(claim, f"为「{claim['title'][:80]}」设计最小可证伪实验", success_signal="预注册指标达到阈值")
                wiki.add_alternative(claim, "观察到的效应可能来自选择偏差或练习量差异")
                split = wiki.evidence_split(claim)
                verdicts[claim_id] = split["verdict"]
                if split["verdict"] == "supported":
                    raise RuntimeError("offline research must never conclude 'supported' without read sources")
        state = {"verdicts": verdicts, "note": "离线运行未读取任何来源 → 诚实输出 insufficient_evidence"}
        path = _write_state(run_dir(ctx), "research", state)
        return _result(state, role="researcher", checks=["experiments_designed", "alternatives_listed", "honest_verdict"], files=[path])

    def memory(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.dual_track_memory import DualTrackMemory
        upstream = _read_state(run_dir(ctx), "research")
        inspire_state = _read_state(run_dir(ctx), "inspire")
        with DualTrackMemory(data / "memory" / "muse.sqlite3") as mem:
            saved = mem.record_agent_experience(
                f"Workflow A {ctx['payload']['plan_id']}: {len(upstream['verdicts'])} 个象征假设已入 Wiki，证据不足，待实验",
                source=f"workflow_a:{ctx['payload']['plan_id']}", tag="experience", kind="case")
        exported = None
        if obsidian_vault is not None and obsidian_vault.is_dir():
            from muse.obsidian_vault import export_research_wiki
            from muse.research_knowledge import ResearchKnowledgeStore
            with ResearchKnowledgeStore(data / "research" / "knowledge.sqlite3") as knowledge:
                exported = export_research_wiki(knowledge, obsidian_vault, node_ids=list(inspire_state["claim_ids"])[:200])
        state = {"agent_memory_id": saved["id"], "track": saved["track"],
                 "obsidian_created": (exported or {}).get("created"), "obsidian_overwritten": (exported or {}).get("existing_notes_overwritten", 0)}
        if state["track"] != "agent":
            raise RuntimeError("workflow memory must land on the Agent Track")
        path = _write_state(run_dir(ctx), "memory", state)
        return _result(state, role="researcher", checks=["agent_track_only", "obsidian_create_only"], files=[path])

    def build(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.initiative_engine import InitiativeStore
        from muse.prototype_builder import StaticPrototypeBuilder
        from muse.publish_gate import validate_artifact
        upstream = _read_state(run_dir(ctx), "inspire")
        with InitiativeStore(data / "initiatives" / "muse.sqlite3") as store:
            initiative = store.propose(
                f"Workflow A 原型：{upstream['question'][:80]}", kind="product",
                creator_value="把象征灵感转为可检验的产品原型", concept="静态原型展示问题、假设与待验证实验",
                first_step="生成静态原型并人工评审", expected_output="本地静态 HTML 原型",
                inspiration_refs=[f"wiki-claim:{c}" for c in upstream["claim_ids"]][:64])
            built = StaticPrototypeBuilder(workspace).build(store, initiative["id"])
        html = workspace / built["relative_path"] / "index.html"
        check = validate_artifact(html)
        if not check["ok"]:
            raise RuntimeError(f"prototype failed structural validation: {check['checks']}")
        state = {"initiative_id": initiative["id"], "prototype": built["relative_path"], "html_sha256": check["sha256"]}
        path = _write_state(run_dir(ctx), "build", state)
        return _result(state, role="designer", checks=["static_html_valid", "no_code_executed"], files=[path, html])

    def evolve(ctx: dict[str, Any]) -> dict[str, Any]:
        research_state = _read_state(run_dir(ctx), "research")
        build_state = _read_state(run_dir(ctx), "build")
        proposal = {
            "kind": "evolution_proposal_draft", "status": "draft_not_applied",
            "observation": f"{len(research_state['verdicts'])} 个假设全部 insufficient_evidence",
            "proposal": "为 Workflow A 增加真实来源检索步骤（需联网授权与人工审批）",
            "baseline": {"prototype": build_state["prototype"], "html_sha256": build_state["html_sha256"]},
            "required_gates": ["baseline_tests", "failing_test_first", "independent_review", "human_approval", "versioned_rollback"],
            "patch_applied": False,
        }
        path = _write_state(run_dir(ctx), "evolve", proposal)
        return _result(proposal, role="architect", checks=["draft_only", "no_patch_applied"], files=[path])

    return {"wf_a.inspire": inspire, "wf_a.research": research, "wf_a.memory": memory,
            "wf_a.build": build, "wf_a.evolve": evolve}


def plan_steps() -> list[dict[str, Any]]:
    return [
        {"key": "inspire", "title": "灵感拆解（象征实验室）", "role": "researcher", "objective": "生成可检验假设", "handler": "wf_a.inspire"},
        {"key": "research", "title": "正反证研究", "role": "researcher", "objective": "设计实验并列出替代解释", "handler": "wf_a.research", "depends_on": ["inspire"]},
        {"key": "memory", "title": "经验沉淀", "role": "researcher", "objective": "记录系统经验", "handler": "wf_a.memory", "depends_on": ["research"]},
        {"key": "build", "title": "原型制作", "role": "writer", "objective": "生成本地静态原型", "handler": "wf_a.build", "depends_on": ["memory"]},
        {"key": "evolve", "title": "受控进化提案", "role": "developer", "objective": "起草进化提案（不应用补丁）", "handler": "wf_a.evolve",
         "depends_on": ["build"], "risk_override": "high"},
    ]


def run_workflow_a(workspace: str | Path, question: str, *, obsidian_vault: str | Path | None = None,
                   plan_id: str | None = None) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    vault = Path(obsidian_vault).resolve() if obsidian_vault else None
    with MuseCommander(ws / "data" / "commander") as commander:
        for name, handler in build_handlers(ws, obsidian_vault=vault).items():
            commander.register_handler(name, handler)
        pid = plan_id or hashlib.sha256(f"{question}|{time.time_ns()}".encode("utf-8")).hexdigest()[:16]
        plan = commander.submit_goal(question, plan_steps(), plan_id=pid)
        outcomes = commander.drain_plan([t["task_id"] for t in plan["tickets"]], timeout_seconds=120)
        statuses = {t["key"]: commander.queue.get(t["task_id"])["status"] for t in plan["tickets"]}
        receipts = commander.verify_receipt_chain()
    return {"plan_id": pid, "statuses": statuses, "tickets": plan["tickets"], "receipts": receipts,
            "run_dir": str(ws / "data" / "workflow_a" / pid),
            "outcomes": [{k: o.get(k) for k in ("status", "task_id")} for o in outcomes]}


def approve_and_finish(workspace: str | Path, plan_result: dict[str, Any], *, approved_by: str) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    with MuseCommander(ws / "data" / "commander") as commander:
        for name, handler in build_handlers(ws).items():
            commander.register_handler(name, handler)
        for ticket in plan_result["tickets"]:
            if commander.queue.get(ticket["task_id"])["status"] == "waiting_approval":
                commander.approve(ticket["task_id"], approved_by=approved_by)
        commander.drain_plan([t["task_id"] for t in plan_result["tickets"]], timeout_seconds=120)
        statuses = {t["key"]: commander.queue.get(t["task_id"])["status"] for t in plan_result["tickets"]}
        receipts = commander.verify_receipt_chain()
    return {"statuses": statuses, "receipts": receipts}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run Workflow A (inspiration → research → memory → prototype → evolution proposal)")
    parser.add_argument("question")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--obsidian-vault", default=None)
    args = parser.parse_args(argv)
    result = run_workflow_a(args.workspace, args.question, obsidian_vault=args.obsidian_vault)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("进化提案步骤需人工批准：在控制台「统帅控制台」或 POST /api/v1/commander/approve 处理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
