from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from muse.initiative_engine import InitiativeStore


class InitiativeStoreTests(unittest.TestCase):
    def test_creator_interest_can_become_a_product_initiative(self):
        with tempfile.TemporaryDirectory() as d:
            with InitiativeStore(Path(d) / "initiatives.sqlite3") as store:
                item = store.propose(
                    "Muse 灵感知识雷达", kind="tool",
                    creator_value="持续发现创造者关注领域的新知识与机会",
                    concept="从 Chrome 书签出发扩展相关网站、资料和潜在产品方向",
                    first_step="对书签域名聚类并生成相关站点候选",
                    expected_output="可持续更新的网站知识地图与每周机会清单",
                    interest_signals=["Chrome 书签", "玄学灵感", "自主进化"],
                    inspiration_refs=["report:metaphysics#1"], priority=95,
                )
                self.assertEqual(item["status"], "proposed")
                self.assertEqual(item["kind"], "tool")
                self.assertEqual(store.summary()["ready_to_prioritize"], 1)
                store.update(item["id"], status="building")
                self.assertEqual(store.summary()["by_status"]["building"], 1)

    def test_update_returns_target_even_when_it_is_outside_default_list_window(self):
        with tempfile.TemporaryDirectory() as d:
            with InitiativeStore(Path(d) / "initiatives.sqlite3") as store:
                args = dict(kind="feature", creator_value="value", concept="concept",
                            first_step="step", expected_output="output")
                items = [store.propose(f"Window item {i:03d}", **args) for i in range(201)]
                target = items[-1]
                self.assertNotIn(target["id"], {item["id"] for item in store.list(limit=200)})
                updated = store.update(target["id"], status="building")
                self.assertEqual(updated["id"], target["id"])
                self.assertEqual(updated["status"], "building")

    def test_duplicate_proposals_are_idempotent_and_invalid_status_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with InitiativeStore(Path(d) / "initiatives.sqlite3") as store:
                args = dict(kind="feature", creator_value="value", concept="concept",
                            first_step="step", expected_output="output")
                first = store.propose("Idea", **args)
                second = store.propose("Idea", **args)
                self.assertEqual(first["id"], second["id"])
                with self.assertRaises(ValueError):
                    store.update(first["id"], status="magic")


    def test_feedback_is_durable_auditable_and_does_not_ship_initiative(self):
        with tempfile.TemporaryDirectory() as d:
            database = Path(d) / "initiatives.sqlite3"
            with InitiativeStore(database) as store:
                item = store.propose(
                    "Feedback target", kind="feature", creator_value="value",
                    concept="concept", first_step="step", expected_output="output",
                )
                first = store.record_feedback(
                    item["id"], "保留原型，但需要补充失败案例。",
                    decision="revise", reviewer="creator", source="prototype_review",
                )
                second = store.record_feedback(
                    item["id"], "修订方向可以接受。", decision="accept",
                    reviewer="creator", source="manual",
                )
                self.assertEqual(first["decision"], "revise")
                self.assertEqual(first["feedback"], "保留原型，但需要补充失败案例。")
                self.assertEqual(first["source"], "prototype_review")
                self.assertNotEqual(first["id"], second["id"])
                self.assertEqual(len(store.list_feedback(initiative_id=item["id"])), 2)
                self.assertEqual(store.list(limit=10)[0]["status"], "proposed")
            with InitiativeStore(database) as reopened:
                self.assertEqual(len(reopened.list_feedback()), 2)
                self.assertEqual(reopened.list_feedback()[0]["initiative_id"], item["id"])
                self.assertEqual(reopened.list(limit=10)[0]["status"], "proposed")

    def test_feedback_rejects_invalid_data_and_unknown_initiatives(self):
        with tempfile.TemporaryDirectory() as d:
            with InitiativeStore(Path(d) / "initiatives.sqlite3") as store:
                item = store.propose(
                    "Validation target", kind="feature", creator_value="value",
                    concept="concept", first_step="step", expected_output="output",
                )
                for kwargs in (
                    {"decision": "maybe"},
                    {"decision": ["accept"]},
                    {"decision": "accept", "reviewer": ""},
                    {"decision": "accept", "source": ""},
                ):
                    with self.assertRaises(ValueError):
                        store.record_feedback(item["id"], "valid feedback", **kwargs)
                with self.assertRaises(ValueError):
                    store.record_feedback(item["id"], "   ", decision="reject")
                with self.assertRaises(KeyError):
                    store.record_feedback("missing", "valid feedback", decision="reject")
                self.assertEqual(store.list_feedback(), [])


    def test_performance_snapshots_are_validated_append_only_and_idempotent(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "initiatives.sqlite3"
            with InitiativeStore(database) as store:
                item = store.propose(
                    "Commercial short", kind="creative_output", creator_value="measurable content",
                    concept="publish and measure", first_step="produce a pilot", expected_output="a short video",
                )
                metrics = {
                    "views": 1200, "likes": 80, "comments": 12, "saves": 30,
                    "shares": 9, "cta_clicks": 15, "qualified_leads": 4, "orders": 1,
                    "reported_revenue_minor": 9900, "settled_revenue_minor": 5000,
                    "production_cost_minor": 1200, "three_second_retention_pct": 72.5,
                    "completion_rate_pct": 31.0, "avg_watch_seconds": 18.4,
                }
                kwargs = dict(
                    idempotency_key="yt-2026-11-17-snapshot-1", platform="youtube",
                    source="official_analytics", currency="SGD", metrics=metrics,
                    published_at="2026-11-16T12:00:00+08:00", source_reference="studio-export-001",
                )
                first = store.record_performance(item["id"], **kwargs)
                replay = store.record_performance(item["id"], **kwargs)
                self.assertEqual(first["id"], replay["id"])
                self.assertEqual(store.aggregate_performance(initiative_id=item["id"])[0]["settled_revenue_minor"], 5000)
                self.assertEqual(store.list_performance(initiative_id=item["id"])[0]["views"], 1200)
                changed = dict(kwargs, metrics={**metrics, "views": 1201})
                with self.assertRaises(ValueError):
                    store.record_performance(item["id"], **changed)
                with self.assertRaises(ValueError):
                    store.record_performance(item["id"], **dict(kwargs, metrics={"completion_rate_pct": 101}))
                with self.assertRaises(ValueError):
                    store.record_performance(item["id"], **dict(kwargs, metrics={"views": -1}))
                with self.assertRaises(KeyError):
                    store.record_performance("f" * 32, **kwargs)
                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(
                        "UPDATE initiative_performance_snapshots SET views=0 WHERE id=?", (first["id"],)
                    )
                store.connection.rollback()
                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(
                        "DELETE FROM initiative_performance_snapshots WHERE id=?", (first["id"],)
                    )
                store.connection.rollback()
            with InitiativeStore(database) as reopened:
                self.assertEqual(reopened.list_performance(initiative_id=item["id"])[0]["views"], 1200)

    def test_performance_content_profile_is_allowlisted_versioned_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                item = store.propose(
                    "Creative profile", kind="creative_output", creator_value="measurable",
                    concept="tag content", first_step="annotate", expected_output="profiled snapshots",
                )
                profile = {
                    "topic": "AI 创作", "hook_style": "反常识提问",
                    "narrative_structure": "问题-冲突-解决", "editing_pace": "快切",
                    "visual_style": "电影写实", "voice_language": "粤语（香港）",
                    "cta_style": "评论引导", "format": "短视频",
                }
                first = store.record_performance(
                    item["id"], idempotency_key="profile-1", platform="youtube",
                    source="manual_entry", currency="USD", source_reference="profile-video",
                    metrics={"views": 1200}, content_profile=profile,
                )
                replay = store.record_performance(
                    item["id"], idempotency_key="profile-1", platform="youtube",
                    source="manual_entry", currency="USD", source_reference="profile-video",
                    metrics={"views": 1200}, content_profile=profile,
                )
                self.assertEqual(first["content_profile"], profile)
                self.assertEqual(replay["id"], first["id"])
                self.assertEqual(store.list_performance(initiative_id=item["id"])[0]["content_profile"], profile)
                with self.assertRaises(ValueError):
                    store.record_performance(
                        item["id"], idempotency_key="profile-1", platform="youtube",
                        source="manual_entry", currency="USD", source_reference="profile-video",
                        metrics={"views": 1200}, content_profile={"topic": "different"},
                    )
                with self.assertRaises(ValueError):
                    store.record_performance(
                        item["id"], idempotency_key="profile-invalid", platform="youtube",
                        source="manual_entry", currency="USD", source_reference="bad-profile",
                        metrics={"views": 1}, content_profile={"unapproved_dimension": "x"},
                    )
                with self.assertRaises(ValueError):
                    store.record_performance(
                        item["id"], idempotency_key="profile-long", platform="youtube",
                        source="manual_entry", currency="USD", source_reference="long-profile",
                        metrics={"views": 1}, content_profile={"topic": "x" * 81},
                    )

    def test_performance_analysis_deduplicates_latest_content_and_separates_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                item = store.propose(
                    "Performance analysis", kind="creative_output", creator_value="measurable",
                    concept="compare content", first_step="publish pilots", expected_output="cohort signals",
                )

                def add(key, reference, *, source="official_analytics", currency="USD",
                        views=1000, likes=100, retention=70.0, settled=500):
                    store.record_performance(
                        item["id"], idempotency_key=key, platform="youtube", source=source,
                        currency=currency, source_reference=reference,
                        metrics={
                            "views": views, "likes": likes,
                            "three_second_retention_pct": retention,
                            "completion_rate_pct": 30.0,
                            "reported_revenue_minor": settled * 2,
                            "settled_revenue_minor": settled,
                        },
                    )

                add("v1-old", "video-1", views=400, likes=20, retention=40.0)
                add("v1-new", "video-1", views=1000, likes=100, retention=70.0, settled=700)
                add("v2", "video-2", views=1000, likes=80, retention=60.0)
                add("v3", "video-3", views=1000, likes=60, retention=50.0)
                add("manual-1", "manual-video", source="manual_entry", currency="EUR", views=1200)
                add("unidentified", "", views=500)

                analysis = store.analyze_performance(initiative_id=item["id"])
                self.assertEqual(analysis["unidentified_snapshots_excluded"], 1)
                self.assertEqual(len(analysis["cohorts"]), 2)
                official = next(c for c in analysis["cohorts"] if c["source"] == "official_analytics")
                self.assertEqual(official["status"], "ready_for_review")
                self.assertEqual(official["sample_count"], 3)
                self.assertEqual(official["total_views"], 3000)
                self.assertAlmostEqual(official["rates"]["three_second_retention_pct"], 60.0)
                self.assertEqual(official["revenue_minor"]["reported"], 3400)

                self.assertEqual(official["revenue_minor"]["settled"], 1700)
                self.assertEqual(official["interpretation"], "descriptive_observation_not_causal_evidence")
                manual = next(c for c in analysis["cohorts"] if c["source"] == "manual_entry")
                self.assertEqual(manual["currency"], "EUR")
                self.assertEqual(manual["status"], "insufficient_sample")
                self.assertIn("unidentified_snapshots_excluded", manual["warnings"])

    def test_performance_analysis_ranks_metric_specific_content_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                item = store.propose(
                    "Rank content examples", kind="creative_output", creator_value="measurable",
                    concept="compare content", first_step="publish pilots", expected_output="metric comparisons",
                )
                for index, retention in enumerate((20, 30, 40, 60, 80, 90), start=1):
                    store.record_performance(
                        item["id"], idempotency_key=f"ranking-{index}", platform="youtube",
                        source="official_analytics", currency="USD",
                        source_reference=f"video-{index}",
                        metrics={
                            "views": 500, "likes": index * 10,
                            "three_second_retention_pct": retention,
                            "completion_rate_pct": retention / 2,
                            "cta_clicks": index,
                        },
                    )
                cohort = store.analyze_performance(initiative_id=item["id"])["cohorts"][0]
                retention = cohort["item_comparisons"]["three_second_retention_pct"]
                self.assertEqual(retention["status"], "descriptive_comparison")
                self.assertEqual(retention["eligible_item_count"], 6)
                self.assertEqual(
                    [entry["source_reference"] for entry in retention["top"]],
                    ["video-6", "video-5", "video-4"],
                )
                self.assertEqual(
                    [entry["source_reference"] for entry in retention["bottom"]],
                    ["video-1", "video-2", "video-3"],
                )
                self.assertIn("not an overall success score", cohort["comparison_note"])

    def test_performance_analysis_marks_small_samples_and_preserves_missing_rates(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                item = store.propose(
                    "Small sample", kind="creative_output", creator_value="value",
                    concept="concept", first_step="step", expected_output="output",
                )
                store.record_performance(
                    item["id"], idempotency_key="one", platform="tiktok",
                    source="manual_entry", currency="USD", source_reference="one-video",
                    metrics={"views": 900, "likes": 9},
                )
                cohort = store.analyze_performance(initiative_id=item["id"])["cohorts"][0]
                self.assertEqual(cohort["status"], "insufficient_sample")
                self.assertEqual(cohort["sample_count"], 1)
                self.assertIsNone(cohort["rates"]["completion_rate_pct"])
                self.assertIsNone(cohort["rates"]["avg_watch_seconds"])
                self.assertIn("insufficient_content_samples", cohort["warnings"])
                self.assertIn("insufficient_views", cohort["warnings"])

    def test_performance_analysis_rejects_invalid_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                item = store.propose(
                    "Threshold validation", kind="creative_output", creator_value="value",
                    concept="concept", first_step="step", expected_output="output",
                )
                with self.assertRaises(ValueError):
                    store.analyze_performance(initiative_id=item["id"], min_sample_count=1)
                with self.assertRaises(ValueError):
                    store.analyze_performance(initiative_id=item["id"], min_views=True)

    def test_initiative_timeline_is_append_only_and_records_lifecycle_without_feedback_body(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "initiatives.sqlite3"
            with InitiativeStore(database) as store:
                args = dict(
                    kind="creative_output", creator_value="把灵感变成作品",
                    concept="跨领域组合并生成可验证的创作候选",
                    first_step="建立创作原型", expected_output="可下载的离线作品",
                )
                item = store.propose("创作闭环", **args)
                duplicate = store.propose("创作闭环", **args)
                self.assertEqual(item["id"], duplicate["id"])
                store.update(item["id"], status="building")
                store.record_feedback(
                    item["id"], "这段反馈正文不应复制进事件摘要",
                    decision="revise", reviewer="creator", source="manual",
                )

                events = store.list_events(initiative_id=item["id"])
                self.assertEqual(
                    [event["event_type"] for event in events],
                    ["created", "status_changed", "feedback_recorded"],
                )
                self.assertEqual(events[1]["from_status"], "proposed")
                self.assertEqual(events[1]["to_status"], "building")
                self.assertEqual(events[2]["source"], "manual")
                self.assertNotIn("这段反馈正文", str(events))
                self.assertEqual(len(store.list_events(initiative_id=item["id"])), 3)

                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(
                        "UPDATE initiative_events SET event_type='forged' WHERE initiative_id=?",
                        (item["id"],),
                    )
                store.connection.rollback()
                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(
                        "DELETE FROM initiative_events WHERE initiative_id=?",
                        (item["id"],),
                    )
                store.connection.rollback()

    def test_product_artifact_event_is_idempotent_and_never_marks_shipped(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                item = store.propose(
                    "作品交付", kind="product", creator_value="可验证的作品",
                    concept="概念", first_step="生成", expected_output="离线产品",
                )
                store.update(item["id"], status="testing")
                store.record_product_artifact(
                    item["id"],
                    relative_path="data/products/example/index.html",
                    package_sha256="a" * 64,
                    validated=True,
                )
                store.record_product_artifact(
                    item["id"],
                    relative_path="data/products/example/index.html",
                    package_sha256="a" * 64,
                    validated=True,
                )
                events = store.list_events(initiative_id=item["id"])
                artifacts = [event for event in events if event["event_type"] == "product_built"]
                self.assertEqual(len(artifacts), 1)
                self.assertEqual(artifacts[0]["to_status"], "testing")
                self.assertEqual(store.list(limit=10)[0]["status"], "testing")

    def test_research_links_are_cycle_scoped_idempotent_and_append_only(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                item = store.propose(
                    "研究与创作关联", kind="creative_output", creator_value="可追溯知识",
                    concept="研究节点关联创作项目", first_step="建立关联", expected_output="统一账本",
                )
                cycle_a = "cycle_" + "a" * 32
                cycle_b = "cycle_" + "b" * 32
                first = store.record_knowledge_link(
                    item["id"], "a" * 32, node_type="claim", relationship="counter_evidence",
                    evolution_cycle_id=cycle_a,
                )
                repeated = store.record_knowledge_link(
                    item["id"], "a" * 32, node_type="claim", relationship="counter_evidence",
                    evolution_cycle_id=cycle_a,
                )
                later = store.record_knowledge_link(
                    item["id"], "a" * 32, node_type="claim", relationship="counter_evidence",
                    evolution_cycle_id=cycle_b,
                )
                self.assertEqual(first["id"], repeated["id"])
                self.assertNotEqual(first["id"], later["id"])
                self.assertEqual(len(store.connection.execute(
                    "SELECT * FROM initiative_knowledge_links WHERE initiative_id=?", (item["id"],)
                ).fetchall()), 2)
                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(
                        "DELETE FROM initiative_knowledge_links WHERE initiative_id=?", (item["id"],)
                    )
                store.connection.rollback()

    def test_video_attempt_claim_is_exclusive_and_expired_lease_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "initiatives.sqlite3"
            with InitiativeStore(database) as store:
                attempt = store.start_video_attempt("worker-attempt", mode="prompt_to_video")
                claimed = store.claim_next_video_attempt("worker-a", lease_seconds=15, now=1000)
                self.assertEqual(claimed["attempt_id"], attempt["attempt_id"])
                self.assertEqual(claimed["status"], "running")
                self.assertEqual(claimed["attempt_count"], 1)
                self.assertIsNone(store.claim_next_video_attempt("worker-b", lease_seconds=15, now=1001))
                recovered = store.claim_next_video_attempt("worker-b", lease_seconds=15, now=1016)
                self.assertEqual(recovered["attempt_id"], attempt["attempt_id"])
                self.assertEqual(recovered["worker_id"], "worker-b")
                self.assertEqual(recovered["attempt_count"], 2)
                events = store.list_video_attempt_events("worker-attempt")
                self.assertEqual([event["event_type"] for event in events], ["queued", "started", "queued", "started"])

    def test_legacy_status_api_start_records_recoverable_worker_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("api-attempt", mode="prompt_to_video")
                running = store.mark_video_attempt_running("api-attempt")
                self.assertEqual(running["status"], "running")
                self.assertEqual(running["worker_id"], "status-api")
                self.assertGreater(running["lease_expires_at"], 0)
                self.assertEqual(running["attempt_count"], 1)

    def test_video_attempt_can_be_released_by_its_worker_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("release-attempt", mode="prompt_to_video")
                store.claim_next_video_attempt("worker-a", lease_seconds=15, now=1000)
                released = store.release_video_attempt("release-attempt", worker_id="worker-a", reason="runtime_paused")
                self.assertEqual(released["status"], "queued")
                self.assertIsNone(released["worker_id"])
                self.assertEqual(store.list_video_attempt_events("release-attempt")[-1]["event_type"], "queued")

    def test_video_attempt_can_be_cancelled_before_worker_claims_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("cancel-attempt", mode="prompt_to_video")
                cancelled = store.cancel_video_attempt("cancel-attempt", reason="creator_paused")
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertIsNone(store.claim_next_video_attempt("worker-a", lease_seconds=15, now=1000))
                events = store.list_video_attempt_events("cancel-attempt")
                self.assertEqual(events[-1]["event_type"], "cancelled")

    def test_failed_video_attempt_can_be_explicitly_retried_with_audit_event(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("retry-attempt", mode="prompt_to_video")
                store.claim_next_video_attempt("worker-a", lease_seconds=15, now=1000)
                store.fail_video_attempt("retry-attempt", failure_code="video.test_failure")
                retried = store.retry_failed_video_attempt("retry-attempt", max_attempts=2)
                self.assertEqual(retried["status"], "queued")
                claimed = store.claim_next_video_attempt("worker-b", lease_seconds=15, now=1010)
                self.assertEqual(claimed["attempt_count"], 2)
                store.fail_video_attempt("retry-attempt", failure_code="video.second_failure")
                with self.assertRaisesRegex(ValueError, "retry budget exhausted"):
                    store.retry_failed_video_attempt("retry-attempt", max_attempts=2)

if __name__ == "__main__":
    unittest.main()
