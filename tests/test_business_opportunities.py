import json, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from muse.business_opportunities import BusinessOpportunityStore, score_opportunity
from muse.initiative_engine import InitiativeStore
from muse.task_queue import TaskQueue
from muse.video_task_worker import VideoTaskWorker
from test_video_task_worker import FakeAdmissionProvider, fake_admission_request_factory
from test_video_task_worker import strict_test_planner


class BusinessOpportunityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/"opportunities.sqlite3"
        self.store=BusinessOpportunityStore(self.db)
        self.planner=strict_test_planner()
        self.ratings=dict(demand_signal=4,willingness_to_pay=3,delivery_fit=5,competition_gap=3,repeatability=4,risk=2)
        self.args=dict(idempotency_key="pilot-1",title="粤语短片服务",customer_segment="本地餐饮商户",
          customer_problem="缺乏持续制作短视频的时间",proposed_offer="每周五条粤语短片",
          validation_test="向三家商户展示样片",success_signal="至少一家预约付费试点",
          ratings=self.ratings,estimated_price_minor=19900,estimated_cost_minor=5000)
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def test_score_is_bounded_and_risk_sensitive(self):
        self.assertTrue(0<=score_opportunity(self.ratings)<=100)
        self.assertGreater(score_opportunity(dict(self.ratings,risk=1)),score_opportunity(self.ratings))
    def test_invalid_ratings_rejected(self):
        for r in ({"demand_signal":5},dict(self.ratings,risk=True),dict(self.ratings,risk=6)):
            with self.assertRaises(ValueError): score_opportunity(r)
    def test_create_idempotency_and_conflict(self):
        a=self.store.create(**self.args); self.assertEqual(a["id"],self.store.create(**self.args)["id"])
        with self.assertRaises(ValueError): self.store.create(**dict(self.args,title="changed"))
    def test_create_replay_returns_latest_evidence(self):
        a=self.store.create(**self.args)
        self.store.add_evidence(a["id"],idempotency_key="observed-1",evidence_type="creator_observation",epistemic_status="observed",summary="手工观察记录")
        replay=self.store.create(**self.args)
        self.assertEqual(len(replay["evidence"]),1)
        self.assertEqual(replay["evidence"][0]["idempotency_key"],"observed-1")

    def test_evidence_classification_and_validation_state(self):
        a=self.store.create(**self.args)
        self.store.add_evidence(a["id"],idempotency_key="official",evidence_type="official_source",epistemic_status="observed",source_url="https://example.com/report",summary="公开行业报告")
        self.store.add_evidence(a["id"],idempotency_key="interview",evidence_type="customer_interview",epistemic_status="reported",summary="客户表示有内容制作困难")
        self.store.add_evidence(a["id"],idempotency_key="estimate",evidence_type="estimate",epistemic_status="assumption",summary="预估制作成本")
        s=self.store.get(a["id"])["evidence_summary"]
        self.assertEqual(s["validation_state"],"evidence_supported_candidate"); self.assertEqual(s["assumptions"],1)

    def test_public_source_requires_safe_http_url(self):
        a=self.store.create(**self.args)
        for i,url in enumerate(("", "file:///etc/passwd", "https://u:p@example.com")):
            with self.assertRaises(ValueError):
                self.store.add_evidence(a["id"],idempotency_key=f"bad-{i}",evidence_type="official_source",epistemic_status="observed",source_url=url,summary="bad")

    def test_evidence_idempotency_and_append_only(self):
        a=self.store.create(**self.args); kw=dict(idempotency_key="src",evidence_type="market_data",epistemic_status="observed",source_url="https://example.com/data",summary="market data")
        e=self.store.add_evidence(a["id"],**kw); self.assertEqual(e["id"],self.store.add_evidence(a["id"],**kw)["id"])
        with self.assertRaises(ValueError): self.store.add_evidence(a["id"],**dict(kw,summary="changed"))
        with self.assertRaises(sqlite3.IntegrityError): self.store.connection.execute("UPDATE business_opportunity_evidence SET summary='x' WHERE id=?",(e["id"],))
        self.store.connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError): self.store.connection.execute("DELETE FROM business_opportunity_evidence WHERE id=?",(e["id"],))
        self.store.connection.rollback()

    def test_shortlist_top_three(self):
        for i in range(4):
            self.store.create(**dict(self.args,idempotency_key=f"p-{i}",title=f"idea-{i}",ratings=dict(self.ratings,demand_signal=i+1)))
        result=self.store.shortlist(); self.assertEqual(len(result),3)
        self.assertEqual(result,sorted(result,key=lambda x:x["score"],reverse=True))

    def test_promote_to_existing_initiative_is_idempotent_draft(self):
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"initiatives.sqlite3"
        first=self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        second=self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        self.assertEqual(first["initiative"]["id"],second["initiative"]["id"])
        self.assertTrue(first["created_link"]); self.assertFalse(second["created_link"])
        self.assertEqual(second["initiative"]["status"],"proposed")
        self.assertEqual(second["initiative"]["autonomy_level"],"draft")
        self.assertIn(f"business-opportunity:{opportunity['id']}",second["initiative"]["inspiration_refs"])
        count=self.store.connection.execute("SELECT COUNT(*) FROM business_opportunity_events WHERE event_type='initiative_linked'").fetchone()[0]
        self.assertEqual(count,1)

    def test_custom_video_brief_is_frozen_into_initiative_and_approval_ticket(self):
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"custom-initiatives.sqlite3"
        task_db=Path(self.tmp.name)/"custom-tasks.sqlite3"
        brief={
            "title":"AI 自动化赚钱",
            "prompt":"以可信、电影感镜头展示 AI 自动化工作流；不得编造收入或客户案例。",
            "negative_prompt":"fabricated earnings, fake testimonials, watermarks",
            "aspect_ratio":"9:16",
            "duration_seconds":5.0,
            "steps":20,
            "resolution_tier":"wan21_480p_master",
            "scenes":[
                {"prompt":"展示真实可验证的自动化工作流，不展示虚构收益。","duration_seconds":2.5},
                {"prompt":"从第二机位展示同一工作流的关键步骤与可复核结果。","duration_seconds":2.5},
            ],
            "narration":{
                "enabled":True,"language":"zh-HK","style":"yujie_canto","segments":3,
                "research_text":"介绍自动化工作流的实际步骤，并明确区分可验证事实与待验证收益。",
                "inspiration":"沉稳、知性、自然的香港粤语讲解。",
                "asr_enabled":True,
            },
        }
        promoted=self.store.promote_to_initiative(
            opportunity["id"],initiative_database=initiative_db,video_brief=brief,
        )
        concept=json.loads(promoted["initiative"]["concept"])
        self.assertEqual(concept["creator_brief"]["video"],brief)
        replay=self.store.promote_to_initiative(
            opportunity["id"],initiative_database=initiative_db,video_brief=brief,
        )
        self.assertEqual(replay["initiative"]["id"],promoted["initiative"]["id"])
        requested=self.store.request_video_approval(
            opportunity["id"],initiative_database=initiative_db,
            task_database=task_db,commander_planner=self.planner,
        )
        self.assertEqual(requested["ticket"]["payload"]["video_brief"],brief)
        changed=dict(brief,prompt="不同的创作提示词")
        with self.assertRaisesRegex(ValueError,"different video brief"):
            self.store.promote_to_initiative(
                opportunity["id"],initiative_database=initiative_db,video_brief=changed,
            )

    def test_custom_video_brief_rejects_unsafe_or_unbounded_fields(self):
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"invalid-brief-initiatives.sqlite3"
        invalid=[
            {"title":"x","prompt":"p","unexpected":"field"},
            {"title":"x","prompt":"p","aspect_ratio":"4:3"},
            {"title":"x","prompt":"p","duration_seconds":6},
            {"title":"x","prompt":"p","scenes":[{"shot_id":"shot_001","prompt":"x","duration_seconds":5.0}],"narration":{"enabled":True,"language":"zh-CN"}},
        ]
        for brief in invalid:
            with self.subTest(brief=brief):
                with self.assertRaises(ValueError):
                    self.store.promote_to_initiative(
                        opportunity["id"],initiative_database=initiative_db,video_brief=brief,
                    )

    def test_video_approval_resolves_initiative_by_durable_link_not_recent_list_window(self):
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"initiatives.sqlite3"
        task_db=Path(self.tmp.name)/"tasks.sqlite3"
        promoted=self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        with patch.object(InitiativeStore,"list",return_value=[]):
            requested=self.store.request_video_approval(opportunity["id"],initiative_database=initiative_db,task_database=task_db,commander_planner=self.planner)
        self.assertEqual(requested["initiative_id"],promoted["initiative"]["id"])
        with TaskQueue(task_db) as queue:
            queue.approve(requested["ticket"]["id"],approved_by="test-operator")
        with patch.object(InitiativeStore,"list",return_value=[]):
            queued=self.store.queue_video_after_approval(opportunity["id"],requested["ticket"]["id"],initiative_database=initiative_db,task_database=task_db)
        self.assertEqual(queued["attempt"]["initiative_id"],promoted["initiative"]["id"])

    def test_strict_planner_model_unavailable_creates_no_approval_ticket(self):
        from muse.commander_planner import CommanderPlanner
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"initiatives.sqlite3"
        task_db=Path(self.tmp.name)/"tasks.sqlite3"
        self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        with self.assertRaisesRegex(RuntimeError,"requires an available model client"):
            self.store.request_video_approval(opportunity["id"],initiative_database=initiative_db,task_database=task_db,commander_planner=CommanderPlanner(None,strict_model=True))
        connection=sqlite3.connect(task_db)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()[0],0)
        finally:
            connection.close()

    def test_strict_planner_invalid_output_creates_no_approval_ticket(self):
        from muse.commander_planner import CommanderPlanner
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"initiatives.sqlite3"
        task_db=Path(self.tmp.name)/"tasks.sqlite3"
        self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        invalid=CommanderPlanner(lambda _goal:{"steps":[{"id":"bad"}]},strict_model=True)
        with self.assertRaises(ValueError):
            self.store.request_video_approval(opportunity["id"],initiative_database=initiative_db,task_database=task_db,commander_planner=invalid)
        connection=sqlite3.connect(task_db)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()[0],0)
        finally:
            connection.close()

    def test_strict_planner_persists_model_source_from_official_plan(self):
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"initiatives.sqlite3"
        task_db=Path(self.tmp.name)/"tasks.sqlite3"
        self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        result=self.store.request_video_approval(opportunity["id"],initiative_database=initiative_db,task_database=task_db,commander_planner=self.planner)
        payload=result["ticket"]["payload"]
        self.assertEqual(payload["commander_plan_source"],"model")
        self.assertEqual(payload["commander_plan_snapshot"]["source"],"model")

    def test_commander_approval_gates_video_attempt(self):
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"data"/"initiatives"/"muse.sqlite3"
        task_db=Path(self.tmp.name)/"data"/"tasks"/"muse.sqlite3"
        self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        requested=self.store.request_video_approval(opportunity["id"],initiative_database=initiative_db,task_database=task_db,commander_planner=self.planner)
        ticket=requested["ticket"]
        self.assertEqual(ticket["status"],"waiting_approval")
        with InitiativeStore(initiative_db) as initiatives:
            self.assertIsNone(initiatives.get_video_attempt(ticket["payload"]["attempt_id"]))
        with self.assertRaises(PermissionError):
            self.store.queue_video_after_approval(opportunity["id"],ticket["id"],initiative_database=initiative_db,task_database=task_db)
        import json
        with TaskQueue(task_db) as queue:
            queue.approve(ticket["id"],approved_by="test-operator")
            approved = queue.get(ticket["id"])
            approved_at = approved["payload"]["_approval"]["approved_at"]
            approved["payload"]["_approval"]["approved_at"] = "2000-01-01T00:00:00+00:00"
            queue._connection.execute(
                "UPDATE tasks SET payload_json=? WHERE id=?",
                (json.dumps(approved["payload"]), ticket["id"]),
            )
            queue._connection.commit()
        with self.assertRaises(PermissionError):
            self.store.queue_video_after_approval(opportunity["id"],ticket["id"],initiative_database=initiative_db,task_database=task_db)
        with TaskQueue(task_db) as queue:
            approved = queue.get(ticket["id"])
            approved["payload"]["_approval"]["approved_at"] = approved_at
            queue._connection.execute(
                "UPDATE tasks SET payload_json=? WHERE id=?",
                (json.dumps(approved["payload"]), ticket["id"]),
            )
            queue._connection.commit()
        queued=self.store.queue_video_after_approval(opportunity["id"],ticket["id"],initiative_database=initiative_db,task_database=task_db)
        replay=self.store.queue_video_after_approval(opportunity["id"],ticket["id"],initiative_database=initiative_db,task_database=task_db)
        self.assertTrue(queued["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(queued["attempt"]["status"],"queued")
        self.assertEqual(queued["gpu_execution"],"not_started")
        class StubBuilder:
            def __init__(self): self.received=None
            def build_from_prompt(self,title,prompt,**kwargs):
                self.received=(title,prompt,kwargs)
                return {"validated":True,"initiative_id":"stub-product","version_id":"stub-version","relative_path":"data/products/stub-product/stub-version","mp4_sha256":"a"*64,"package_sha256":"b"*64,"manifest_sha256":"c"*64,"quality_gate":{"status":"passed"}}
            def verify_path(self,products_root,initiative_id,version_id):
                return {"validated":True,"initiative_id":initiative_id,"version_id":version_id,"relative_path":"data/products/stub-product/stub-version","mp4_sha256":"a"*64,"package_sha256":"b"*64,"manifest_sha256":"c"*64}
        tts_packages=Path(self.tmp.name)/".muse-canto-tts-py314"/"canto_tts"
        tts_packages.mkdir(parents=True)
        tts_model=Path(self.tmp.name)/"data"/"models"/"canto-tts-nano"
        tts_model.mkdir(parents=True)
        (tts_model/"browser_poc_manifest.json").write_text("{}",encoding="utf-8")
        builder=StubBuilder()
        with patch("muse.cantonese_tts.preflight_cantonese_tts_runtime",return_value={"status":"ready"}):
            result=VideoTaskWorker(
                Path(self.tmp.name), builder=builder, admission_provider=FakeAdmissionProvider(),
                admission_request_factory=fake_admission_request_factory,
            )._run_approved_attempt()
        self.assertEqual(result["status"],"succeeded",result)
        self.assertTrue(result["content_review_required"])
        self.assertFalse(result["auto_publish"])
        self.assertEqual(builder.received[2]["creator_initiative_id"],queued["attempt"]["initiative_id"])
        self.assertEqual(builder.received[2]["narration_model_dir"],tts_model.resolve())
        self.assertIn("係㗎",builder.received[2]["narration_script"])
        self.assertIn("Do not invent customer testimonials",builder.received[1])

    def test_performance_feedback_reuses_existing_initiative_ledger(self):
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"initiatives.sqlite3"
        promoted=self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        with InitiativeStore(initiative_db) as initiatives:
            snapshot=initiatives.record_performance(promoted["initiative"]["id"],idempotency_key="manual-feedback-1",platform="youtube",source="manual_entry",currency="USD",metrics={"views":100,"qualified_leads":2,"orders":1,"settled_revenue_minor":19900,"production_cost_minor":5000})
            aggregate=initiatives.aggregate_performance(initiative_id=promoted["initiative"]["id"])[0]
        self.assertEqual(snapshot["settled_revenue_minor"],19900)
        self.assertEqual(aggregate["orders"],1)
        self.assertEqual(aggregate["production_cost_minor"],5000)

    def test_rejected_opportunity_cannot_promote(self):
        opportunity=self.store.create(**self.args); self.store.set_status(opportunity["id"],"rejected")
        with self.assertRaises(ValueError): self.store.promote_to_initiative(opportunity["id"],initiative_database=Path(self.tmp.name)/"initiatives.sqlite3")

    def test_rejected_initiative_cannot_request_video_approval(self):
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"initiatives.sqlite3"
        task_db=Path(self.tmp.name)/"tasks.sqlite3"
        promoted=self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        with InitiativeStore(initiative_db) as initiatives:
            initiatives.update(promoted["initiative"]["id"],status="rejected",actor="test",source="test")
        with self.assertRaises(ValueError):
            self.store.request_video_approval(opportunity["id"],initiative_database=initiative_db,task_database=task_db,commander_planner=self.planner)

    def test_status_audit_and_reopen(self):
        a=self.store.create(**self.args); self.store.set_status(a["id"],"researching")
        self.assertEqual(self.store.get(a["id"])["status"],"researching")
        with self.assertRaises(ValueError): self.store.set_status(a["id"],"published")
        self.store.close(); reopened=BusinessOpportunityStore(self.db)
        try:
            self.assertEqual(reopened.get(a["id"])["title"],"粤语短片服务")
            self.assertEqual(reopened.connection.execute("SELECT COUNT(*) FROM business_opportunity_events").fetchone()[0],2)
        finally: reopened.close()

    def test_request_video_approval_freezes_plan_snapshot_and_dag_digest(self):
        """Approval ticket must carry a frozen CommanderPlan snapshot with valid DAG digest."""
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"initiatives.sqlite3"
        task_db=Path(self.tmp.name)/"tasks.sqlite3"
        self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        result=self.store.request_video_approval(opportunity["id"],initiative_database=initiative_db,task_database=task_db,commander_planner=self.planner)
        ticket=result["ticket"]
        payload=ticket["payload"]
        # Commander plan snapshot must be present and self-consistent.
        self.assertIn("commander_plan_id",payload)
        self.assertIn("commander_plan_source",payload)
        self.assertIn("commander_plan_snapshot",payload)
        self.assertIn("dag_sha256",payload)
        snapshot=payload["commander_plan_snapshot"]
        self.assertEqual(snapshot["schema"],"muse-commander-plan-snapshot-v1")
        self.assertEqual(snapshot["plan_id"],payload["commander_plan_id"])
        self.assertEqual(snapshot["source"],payload["commander_plan_source"])
        # DAG digest must be verifiable.
        import hashlib,json
        encoded=json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),payload["dag_sha256"])
        # Must have exactly one reviewer step and a valid structure.
        self.assertTrue(any(s["role"]=="reviewer" for s in snapshot["steps"]))
        self.assertGreaterEqual(len(snapshot["steps"]),2)

    def test_queue_video_after_approval_enqueues_video_pipeline_execution_ticket(self):
        """After approval, an idempotent video_pipeline execution ticket must be created."""
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"data"/"initiatives"/"muse.sqlite3"
        task_db=Path(self.tmp.name)/"data"/"tasks"/"muse.sqlite3"
        self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        requested=self.store.request_video_approval(opportunity["id"],initiative_database=initiative_db,task_database=task_db,commander_planner=self.planner)
        with TaskQueue(task_db) as queue:
            queue.approve(requested["ticket"]["id"],approved_by="test-operator")
        queued=self.store.queue_video_after_approval(opportunity["id"],requested["ticket"]["id"],initiative_database=initiative_db,task_database=task_db)
        # Must return execution_ticket_id.
        self.assertIn("execution_ticket_id",queued)
        exec_id=queued["execution_ticket_id"]
        self.assertIsInstance(exec_id,str)
        self.assertTrue(exec_id)
        # Execution ticket must have kind=video_pipeline and correct bindings.
        with TaskQueue(task_db) as queue:
            exec_ticket=queue.get(exec_id)
        self.assertIsNotNone(exec_ticket)
        ep=exec_ticket["payload"]
        self.assertEqual(ep["kind"],"video_pipeline")
        self.assertEqual(ep["approval_task_id"],requested["ticket"]["id"])
        self.assertEqual(ep["attempt_id"],queued["attempt"]["attempt_id"])
        self.assertEqual(ep["initiative_id"],queued["attempt"]["initiative_id"])
        self.assertIn("commander_plan_snapshot",ep)
        self.assertIn("dag_sha256",ep)
        self.assertIn("commander_plan_id",ep)
        self.assertIn("approval_event_id",ep)
        # Idempotent replay must return same ticket.
        replay=self.store.queue_video_after_approval(opportunity["id"],requested["ticket"]["id"],initiative_database=initiative_db,task_database=task_db)
        self.assertEqual(replay["execution_ticket_id"],exec_id)

    def test_video_pipeline_execution_ticket_dag_digest_matches_approval_ticket(self):
        """Execution ticket DAG digest must match the approval ticket snapshot exactly."""
        opportunity=self.store.create(**self.args)
        initiative_db=Path(self.tmp.name)/"data"/"initiatives"/"muse.sqlite3"
        task_db=Path(self.tmp.name)/"data"/"tasks"/"muse.sqlite3"
        self.store.promote_to_initiative(opportunity["id"],initiative_database=initiative_db)
        requested=self.store.request_video_approval(opportunity["id"],initiative_database=initiative_db,task_database=task_db,commander_planner=self.planner)
        with TaskQueue(task_db) as queue:
            queue.approve(requested["ticket"]["id"],approved_by="test-operator")
        queued=self.store.queue_video_after_approval(opportunity["id"],requested["ticket"]["id"],initiative_database=initiative_db,task_database=task_db)
        with TaskQueue(task_db) as queue:
            exec_ticket=queue.get(queued["execution_ticket_id"])
        ep=exec_ticket["payload"]
        ap=requested["ticket"]["payload"]
        # Plan snapshot and digest must be identical between approval and execution tickets.
        self.assertEqual(ep["commander_plan_snapshot"],ap["commander_plan_snapshot"])
        self.assertEqual(ep["dag_sha256"],ap["dag_sha256"])
        self.assertEqual(ep["commander_plan_id"],ap["commander_plan_id"])
        self.assertEqual(ep["brief_sha256"],ap["video_brief_sha256"])

if __name__=="__main__": unittest.main()
