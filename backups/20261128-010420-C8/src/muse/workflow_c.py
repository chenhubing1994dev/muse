"""Workflow C — 价值创造与商业主权主干, executed as Muse Commander tickets (T3).

opportunity → produce → cost → bundle → revenue, plus a separate high-risk ``publish`` step.

* opportunity: BusinessOpportunityStore record + mandatory counter-evidence + unit-economics
  *simulation* (assumptions only, never a forecast).
* produce: InitiativeStore initiative → StaticPrototypeBuilder landing prototype + offer brief.
* cost: real rows of this plan from the Commander usage ledger (estimated) — no invented numbers.
* bundle: local Publish-Ready Bundle via :mod:`muse.publish_gate` (``publish_allowed`` stays False).
* revenue: honest write-back — without a verified settlement receipt revenue is ``unknown`` and
  ``verified: false``; only an Agent Track experience is recorded (never User Track).
* publish (risk=high): waits for a ``user:``/``human:`` approval; even then Muse only marks the bundle
  ``approved_for_manual_publish`` — it never uploads, posts or charges anything.

Steps hand over state through ``data/workflow_c/<plan_id>/<step>.json`` (disk is the truth).
"""
from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from muse.muse_commander import MuseCommander

STEPS = ("opportunity", "produce", "cost", "bundle", "revenue", "publish")


def _write_state(run_dir: Path, step: str, data: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"{step}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _read_state(run_dir: Path, step: str) -> dict[str, Any]:
    path = run_dir / f"{step}.json"
    if not path.is_file():
        raise FileNotFoundError(f"upstream step state missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _result(obj: Any, *, role: str, checks: list[str], files: list[Path]) -> dict[str, Any]:
    return {"artifact": json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str),
            "builder_id": f"worker:{role}", "evidence": "; ".join(f"{c}: PASS" for c in checks),
            "files": [str(f) for f in files], "output": obj,
            "usage": {"provider": "local_deterministic", "model": "none", "token_source": "not_reported"}}


def build_handlers(workspace: Path) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    data = workspace / "data"

    def run_dir(ctx: dict[str, Any]) -> Path:
        return data / "workflow_c" / str(ctx["payload"]["plan_id"])

    def opportunity(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.business_opportunities import BusinessOpportunityStore
        from muse.unit_economics import simulate
        question = str(ctx["payload"]["goal"])
        plan_id = str(ctx["payload"]["plan_id"])
        counter = {"free_alternatives": "未调研（离线运行）：可能存在免费替代品",
                   "switching_friction": "未调研：用户迁移成本未知",
                   "distribution_difficulty": "未调研：获客渠道未验证",
                   "compliance_risk": "未调研：需人工核对平台与版权规则"}
        sim = simulate({
            "title": question[:200], "price_hypothesis": "9.90", "currency": "USD",
            "payment_fee_rate": "0.03", "payment_fee_fixed": "0.30", "platform_fee_rate": "0.10",
            "refund_rate": "0.05", "compute_cost_per_unit": "0.00", "build_hours": "4", "hourly_cost": "0",
            "fixed_costs": "0", "scenario_units": [0, 10, 100], "counter_evidence": counter,
            "kill_criteria": [{"metric": "settled_orders", "operator": "<", "threshold": "1", "by_day": 30}]})
        with BusinessOpportunityStore(data / "business" / "opportunities.sqlite3") as store:
            item = store.create(idempotency_key=f"wf-c:{plan_id}", title=question[:200],
                                customer_segment="待验证：个人创作者", customer_problem=question[:2000],
                                proposed_offer="本地原型 + 交付包（待人工验证需求）", validation_test="人工访谈 5 位目标用户",
                                success_signal="≥2 位愿意付费试用", ratings={"demand_signal": 3, "willingness_to_pay": 2,
                                "delivery_fit": 4, "competition_gap": 2, "repeatability": 3, "risk": 3})
            store.add_evidence(item["id"], idempotency_key=f"wf-c:{plan_id}:assumption", evidence_type="estimate",
                               epistemic_status="assumption", summary="评分为离线假设，未经任何真实需求数据验证")
        state = {"opportunity_id": item["id"], "score": item["score"], "epistemic_tag": "hypothesis",
                 "is_forecast": False, "counter_evidence": counter, "unit_economics_simulation": sim}
        path = _write_state(run_dir(ctx), "opportunity", state)
        return _result(state, role="market_analyst", checks=["counter_evidence_required", "simulation_not_forecast"], files=[path])

    def produce(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.initiative_engine import InitiativeStore
        from muse.prototype_builder import StaticPrototypeBuilder
        from muse.publish_gate import validate_artifact
        upstream = _read_state(run_dir(ctx), "opportunity")
        question = str(ctx["payload"]["goal"])
        with InitiativeStore(data / "initiatives" / "muse.sqlite3") as store:
            initiative = store.propose(
                f"Workflow C 原型：{question[:80]}", kind="product", creator_value="验证一个可交付的价值主张",
                concept="静态落地页说明问题、方案与待验证假设", first_step="人工评审原型并访谈用户",
                expected_output="本地静态 HTML 原型", inspiration_refs=[f"opportunity:{upstream['opportunity_id']}"])
            built = StaticPrototypeBuilder(workspace).build(store, initiative["id"])
        html = workspace / built["relative_path"] / "index.html"
        brief = run_dir(ctx) / "offer_brief.md"
        brief.write_text(f"# 交付说明\n\n- 问题：{question}\n- 机会评分（启发式，非预测）：{upstream['score']}\n"
                         f"- 反证据：{'；'.join(upstream['counter_evidence'].values())}\n- 收益：unknown（未验证）\n",
                         encoding="utf-8")
        checks = [validate_artifact(html), validate_artifact(brief)]
        if not all(c["ok"] for c in checks):
            raise RuntimeError(f"produced artifacts failed validation: {[c['checks'] for c in checks]}")
        state = {"initiative_id": initiative["id"], "prototype_html": str(html), "brief": str(brief),
                 "sha256": {Path(c["path"]).name: c["sha256"] for c in checks}}
        path = _write_state(run_dir(ctx), "produce", state)
        return _result(state, role="product_manager", checks=["prototype_valid", "brief_valid"], files=[path, html, brief])

    def cost(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.usage_ledger import UsageLedger
        plan_id = str(ctx["payload"]["plan_id"])
        ledger = UsageLedger(Path(ctx["commander_root"]) / "usage.sqlite3")
        try:
            events = ledger.list_events(project_id=plan_id, limit=500)
        finally:
            ledger.close()
        total = sum((Decimal(str(e.get("cost_amount") or "0")) for e in events), Decimal("0"))
        state = {"usage_rows": len(events), "total_cost_usd": str(total), "cost_kind": "estimated",
                 "source": "commander_usage_ledger", "note": "仅统计本计划真实记账行；未记账的外部成本未知"}
        if not events:
            raise RuntimeError("no usage rows recorded for this plan; cost accounting would be fabricated")
        path = _write_state(run_dir(ctx), "cost", state)
        return _result(state, role="finance_analyst", checks=["ledger_rows_counted", "no_invented_costs"], files=[path])

    def bundle(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.publish_gate import build_publish_bundle
        produce_state = _read_state(run_dir(ctx), "produce")
        cost_state = _read_state(run_dir(ctx), "cost")
        econ = run_dir(ctx) / "unit_economics.json"
        econ.write_text(json.dumps(_read_state(run_dir(ctx), "opportunity")["unit_economics_simulation"],
                                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        manifest = build_publish_bundle(run_dir(ctx) / "bundle",
                                        [produce_state["prototype_html"], produce_state["brief"], econ],
                                        title=str(ctx["payload"]["goal"])[:120], channel="local_only",
                                        estimated_cost_usd=cost_state["total_cost_usd"])
        if not manifest["all_artifacts_valid"] or manifest["publish_allowed"] is not False:
            raise RuntimeError("bundle invalid or publish gate open")
        state = {"bundle_dir": str(run_dir(ctx) / "bundle"), "manifest": manifest["manifest_path"],
                 "status": manifest["status"], "publish_allowed": manifest["publish_allowed"]}
        path = _write_state(run_dir(ctx), "bundle", state)
        return _result(state, role="operator", checks=["artifacts_valid", "publish_gate_closed"], files=[path])

    def revenue(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.dual_track_memory import DualTrackMemory
        from muse.revenue_ledger import RevenueLedger
        plan_id = str(ctx["payload"]["plan_id"])
        ledger = RevenueLedger(data / "revenue" / "ledger.sqlite3")
        try:
            summary = ledger.summary("USD")
            chain = ledger.verify_chain()
        finally:
            ledger.close()
        settled = int(summary.get("verified_settlements") or 0) if isinstance(summary, dict) else 0
        state = {"confirmed_revenue": "unknown" if settled == 0 else summary, "verified": settled > 0,
                 "settled_receipts": settled, "ledger_chain": chain, "exposure_is_not_revenue": True,
                 "note": "没有经验证的结算回执 → 收益记为 unknown，未编造任何数字"}
        with DualTrackMemory(data / "memory" / "muse.sqlite3") as mem:
            saved = mem.record_agent_experience(
                f"Workflow C {plan_id}: 交付包已生成，收益 unknown（无回执），待人工验证需求",
                source=f"workflow_c:{plan_id}", tag="experience", kind="case")
        if saved["track"] != "agent":
            raise RuntimeError("revenue write-back must land on the Agent Track")
        state["agent_memory_id"] = saved["id"]
        path = _write_state(run_dir(ctx), "revenue", state)
        return _result(state, role="finance_analyst", checks=["no_fabricated_revenue", "agent_track_only"], files=[path])

    def publish(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.publish_gate import approve_bundle_for_manual_publish
        from muse.task_queue import TaskQueue
        with TaskQueue(Path(ctx["commander_root"]) / "tasks.sqlite3") as queue:
            decisions = [e for e in queue.events(ctx["task_id"], limit=200) if e["event_type"] == "human.decision"]
        approver = decisions[-1]["actor"] if decisions else ""
        if not approver.startswith(("user:", "human:")):
            raise PermissionError("publish step requires a recorded human approval")
        bundle_state = _read_state(run_dir(ctx), "bundle")
        manifest = approve_bundle_for_manual_publish(bundle_state["bundle_dir"], approver=approver)
        state = {"published_by_muse": False, "publish_mode": manifest["publish_mode"], "approved_by": approver,
                 "publish_allowed": manifest["publish_allowed"], "note": "Muse 不会自动上传或发帖；请人工发布"}
        path = _write_state(run_dir(ctx), "publish", state)
        return _result(state, role="operator", checks=["human_approved", "no_external_action"], files=[path])

    def video_bundle(ctx: dict[str, Any]) -> dict[str, Any]:
        from muse.publish_gate import bundle_from_video_package
        args = ctx["payload"].get("route_args") or {}
        package_dir = resolve_video_package_dir(workspace, initiative_id=str(args.get("initiative_id", "")),
                                                package_sha256=str(args.get("package_sha256", "")),
                                                relative_path=args.get("relative_path"))
        manifest = bundle_from_video_package(package_dir, run_dir(ctx) / "bundle", title=str(ctx["payload"]["goal"])[:120],
                                             channel="manual_upload")
        if not manifest["all_artifacts_valid"] or manifest["publish_allowed"] is not False:
            raise RuntimeError(f"video package not bundle-ready: missing={manifest.get('missing_required')} "
                               f"checks={[a['checks'] for a in manifest['artifacts'] if not a['ok']]}")
        state = {"bundle_dir": str(run_dir(ctx) / "bundle"), "manifest": manifest["manifest_path"],
                 "status": manifest["status"], "publish_allowed": False, "source_video_package": str(package_dir),
                 "package_sha256": args.get("package_sha256")}
        path = _write_state(run_dir(ctx), "bundle", state)
        return _result(state, role="operator", checks=["video_package_valid", "publish_gate_closed"], files=[path])

    return {"wf_c.opportunity": opportunity, "wf_c.produce": produce, "wf_c.cost": cost,
            "wf_c.bundle": bundle, "wf_c.revenue": revenue, "wf_c.publish": publish, "wf_c.video_bundle": video_bundle}


def plan_steps() -> list[dict[str, Any]]:
    return [
        {"key": "opportunity", "title": "商业机会评估", "role": "researcher", "objective": "评估机会并列出反证据", "handler": "wf_c.opportunity"},
        {"key": "produce", "title": "原型制作", "role": "writer", "objective": "制作本地静态原型与说明", "handler": "wf_c.produce", "depends_on": ["opportunity"]},
        {"key": "cost", "title": "成本核算", "role": "researcher", "objective": "统计本计划记账成本", "handler": "wf_c.cost", "depends_on": ["produce"]},
        {"key": "bundle", "title": "本地交付包", "role": "writer", "objective": "生成本地交付包（不对外）", "handler": "wf_c.bundle", "depends_on": ["cost"]},
        {"key": "revenue", "title": "收益回写", "role": "researcher", "objective": "收益回写（无回执记 unknown）", "handler": "wf_c.revenue", "depends_on": ["bundle"]},
        {"key": "publish", "title": "人工发布确认", "role": "operator", "objective": "发布到外部平台", "handler": "wf_c.publish",
         "depends_on": ["bundle"], "risk_override": "high"},
    ]


def run_workflow_c(workspace: str | Path, question: str, *, plan_id: str | None = None) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    with MuseCommander(ws / "data" / "commander") as commander:
        for name, handler in build_handlers(ws).items():
            commander.register_handler(name, handler)
        pid = plan_id or hashlib.sha256(f"wf-c|{question}|{time.time_ns()}".encode("utf-8")).hexdigest()[:16]
        plan = commander.submit_goal(question, plan_steps(), plan_id=pid)
        outcomes = commander.drain_plan([t["task_id"] for t in plan["tickets"]], timeout_seconds=120)
        statuses = {t["key"]: commander.queue.get(t["task_id"])["status"] for t in plan["tickets"]}
        receipts = commander.verify_receipt_chain()
    return {"plan_id": pid, "statuses": statuses, "tickets": plan["tickets"], "receipts": receipts,
            "run_dir": str(ws / "data" / "workflow_c" / pid),
            "outcomes": [{k: o.get(k) for k in ("status", "task_id")} for o in outcomes]}


def approve_publish(workspace: str | Path, plan_result: dict[str, Any], *, approved_by: str) -> dict[str, Any]:
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



def resolve_video_package_dir(workspace: str | Path, *, initiative_id: str, package_sha256: str,
                              relative_path: str | None = None) -> Path:
    """Locate a completed video product directory (prefers its ``publish-package-v1`` sub-folder)."""
    ws = Path(workspace).resolve()
    rel = relative_path
    if not rel:
        import sqlite3
        db = ws / "data" / "initiatives" / "muse.sqlite3"
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            for (details_json,) in con.execute(
                    "SELECT details_json FROM initiative_events WHERE initiative_id=? AND event_type='product_built' "
                    "ORDER BY created_at DESC", (initiative_id,)):
                details = json.loads(details_json or "{}")
                if details.get("package_sha256") == package_sha256:
                    rel = details.get("relative_path")
                    break
        finally:
            con.close()
    if not rel:
        raise LookupError("no product_built event matches the completed video package")
    product = (ws / str(rel)).resolve()
    if not product.is_relative_to(ws / "data" / "products"):
        raise PermissionError("video product path escapes data/products")
    sub = product / "publish-package-v1"
    return sub if (sub / "video.mp4").is_file() else product


def video_plan_steps() -> list[dict[str, Any]]:
    """Reuses Workflow C's revenue and publish handlers; only the bundle source differs."""
    return [
        {"key": "bundle", "title": "视频成片交付包", "role": "writer", "objective": "生成本地视频交付包（不对外）", "handler": "wf_c.video_bundle"},
        {"key": "revenue", "title": "收益回写", "role": "researcher", "objective": "收益回写（无回执记 unknown）", "handler": "wf_c.revenue", "depends_on": ["bundle"]},
        {"key": "publish", "title": "人工发布确认", "role": "operator", "objective": "发布到外部平台", "handler": "wf_c.publish",
         "depends_on": ["bundle"], "risk_override": "high"},
    ]


def on_video_completed(workspace: str | Path, *, initiative_id: str, package_sha256: str,
                       relative_path: str | None = None, title: str = "视频成片") -> dict[str, Any]:
    """Idempotent: one Commander plan per completed video package (plan id derived from its hash)."""
    ws = Path(workspace).resolve()
    pid = hashlib.sha256(f"wf-c-video|{package_sha256}".encode("utf-8")).hexdigest()[:16]
    steps = video_plan_steps()
    steps[0]["route_args"] = {"initiative_id": initiative_id, "package_sha256": package_sha256,
                              "relative_path": relative_path}
    with MuseCommander(ws / "data" / "commander") as commander:
        for name, handler in build_handlers(ws).items():
            commander.register_handler(name, handler)
        plan = commander.submit_goal(title, steps, plan_id=pid)
        commander.drain_plan([t["task_id"] for t in plan["tickets"]], timeout_seconds=120)
        statuses = {t["key"]: commander.queue.get(t["task_id"])["status"] for t in plan["tickets"]}
        receipts = commander.verify_receipt_chain()
    return {"plan_id": pid, "statuses": statuses, "tickets": plan["tickets"], "receipts": receipts,
            "run_dir": str(ws / "data" / "workflow_c" / pid)}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run Workflow C (opportunity → produce → cost → bundle → revenue; publish waits for a human)")
    parser.add_argument("question")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args(argv)
    result = run_workflow_c(args.workspace, args.question)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("“人工发布确认”步骤需你在控制台「统帅控制台」批准；批准后 Muse 仍不会自动对外发布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
