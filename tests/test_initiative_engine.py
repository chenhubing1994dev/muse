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
                released = store.release_video_attempt("release-attempt", worker_id="worker-a", reason="runtime_paused", now=1001)
                self.assertEqual(released["status"], "queued")
                self.assertIsNone(released["worker_id"])
                self.assertEqual(store.list_video_attempt_events("release-attempt")[-1]["event_type"], "queued")

    def test_stale_same_worker_id_cannot_complete_fail_or_release_a_new_lease_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("fenced-attempt", mode="prompt_to_video")
                first = store.claim_next_video_attempt("same-worker", lease_seconds=15, now=1000)
                self.assertEqual(first["attempt_count"], 1)
                second = store.claim_next_video_attempt("same-worker", lease_seconds=60, now=1016)
                self.assertEqual(second["attempt_count"], 2)
                artifact = {
                    "product_id": "product", "version_id": "version",
                    "mp4_sha256": "a" * 64, "package_sha256": "b" * 64,
                }
                with self.assertRaisesRegex(ValueError, "unexpired lease epoch"):
                    store.complete_video_attempt(
                        "fenced-attempt", **artifact, worker_id="same-worker", lease_epoch=1, now=1017,
                    )
                with self.assertRaisesRegex(ValueError, "unexpired lease epoch"):
                    store.fail_video_attempt(
                        "fenced-attempt", worker_id="same-worker", lease_epoch=1, now=1017,
                    )
                with self.assertRaisesRegex(ValueError, "unexpired lease epoch"):
                    store.release_video_attempt(
                        "fenced-attempt", worker_id="same-worker", lease_epoch=1, now=1017,
                    )
                current = store.get_video_attempt("fenced-attempt")
                self.assertEqual(current["status"], "running")
                self.assertEqual(current["attempt_count"], 2)
                self.assertEqual(current["worker_id"], "same-worker")
                failed = store.fail_video_attempt(
                    "fenced-attempt", worker_id="same-worker", lease_epoch=2, now=1017,
                )
                self.assertEqual(failed["status"], "failed")

    def test_expired_attempt_requeued_by_another_claim_cannot_be_terminalized_by_old_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("other-first", mode="prompt_to_video", approval_task_id="approval-other-first")
                store.start_video_attempt("expired-second", mode="prompt_to_video", approval_task_id="approval-expired-second")
                old = store.claim_next_video_attempt(
                    "worker-a", lease_seconds=15, now=1000, attempt_id="expired-second",
                )
                self.assertEqual(old["attempt_count"], 1)
                other = store.claim_next_video_attempt(
                    "worker-b", lease_seconds=60, now=1016, attempt_id="other-first",
                )
                self.assertEqual(other["attempt_id"], "other-first")
                expired = store.get_video_attempt("expired-second")
                self.assertEqual(expired["status"], "queued")
                self.assertEqual(expired["attempt_count"], 1)
                with self.assertRaisesRegex(ValueError, "prior worker lease"):
                    store.complete_video_attempt(
                        "expired-second", product_id="product", version_id="version",
                        mp4_sha256="a" * 64, package_sha256="b" * 64,
                        worker_id="worker-a", lease_epoch=1, now=1017,
                    )
                with self.assertRaisesRegex(ValueError, "prior worker lease"):
                    store.fail_video_attempt(
                        "expired-second", worker_id="worker-a", lease_epoch=1, now=1017,
                    )
                unchanged = store.get_video_attempt("expired-second")
                self.assertEqual(unchanged["status"], "queued")
                self.assertIsNone(unchanged["worker_id"])

    def test_expired_running_video_attempt_cannot_complete_fail_or_release(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("expired-running", mode="prompt_to_video")
                claimed = store.claim_next_video_attempt("worker-a", lease_seconds=15, now=1000)
                self.assertEqual(claimed["attempt_count"], 1)
                artifact = {
                    "product_id": "product", "version_id": "version",
                    "mp4_sha256": "a" * 64, "package_sha256": "b" * 64,
                }
                with self.assertRaisesRegex(ValueError, "unexpired lease epoch"):
                    store.complete_video_attempt(
                        "expired-running", **artifact, worker_id="worker-a", lease_epoch=1, now=1016,
                    )
                with self.assertRaisesRegex(ValueError, "unexpired lease epoch"):
                    store.fail_video_attempt(
                        "expired-running", worker_id="worker-a", lease_epoch=1, now=1016,
                    )
                with self.assertRaisesRegex(ValueError, "unexpired lease epoch"):
                    store.release_video_attempt(
                        "expired-running", worker_id="worker-a", lease_epoch=1, now=1016,
                    )
                unchanged = store.get_video_attempt("expired-running")
                self.assertEqual(unchanged["status"], "running")
                self.assertEqual(unchanged["worker_id"], "worker-a")
                self.assertEqual(unchanged["attempt_count"], 1)

    def test_terminal_lease_check_uses_clock_after_waiting_for_write_lock(self):
        import sqlite3
        import threading
        import time
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "initiatives.sqlite3"
            with InitiativeStore(database) as store:
                store.start_video_attempt("lock-delayed-terminal", mode="prompt_to_video")
                store.claim_next_video_attempt("worker-a", lease_seconds=15, now=1000)
                lock_connection = sqlite3.connect(database, timeout=5, check_same_thread=False)
                lock_connection.execute("BEGIN IMMEDIATE")
                lock_released = threading.Event()

                def release_lock():
                    time.sleep(0.05)
                    lock_released.set()
                    lock_connection.commit()

                release_thread = threading.Thread(target=release_lock, daemon=True)
                release_thread.start()

                def clock_after_lock_wait():
                    return 1016.0 if lock_released.is_set() else 1000.0

                try:
                    with patch("muse.initiative_engine.time.time", side_effect=clock_after_lock_wait):
                        with self.assertRaisesRegex(ValueError, "unexpired lease epoch"):
                            store.complete_video_attempt(
                                "lock-delayed-terminal", product_id="product", version_id="version",
                                mp4_sha256="a" * 64, package_sha256="b" * 64,
                                worker_id="worker-a", lease_epoch=1,
                            )
                finally:
                    release_thread.join(timeout=2)
                    lock_connection.close()
                self.assertFalse(release_thread.is_alive())
                unchanged = store.get_video_attempt("lock-delayed-terminal")
                self.assertEqual(unchanged["status"], "running")
                self.assertEqual(unchanged["attempt_count"], 1)

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
                store.fail_video_attempt("retry-attempt", failure_code="video.test_failure", now=1001)
                retried = store.retry_failed_video_attempt("retry-attempt", max_attempts=2)
                self.assertEqual(retried["status"], "queued")
                claimed = store.claim_next_video_attempt("worker-b", lease_seconds=15, now=1010)
                self.assertEqual(claimed["attempt_count"], 2)
                store.fail_video_attempt("retry-attempt", failure_code="video.second_failure", now=1011)
                with self.assertRaisesRegex(ValueError, "retry budget exhausted"):
                    store.retry_failed_video_attempt("retry-attempt", max_attempts=2)

    def test_video_stage_checkpoints_are_idempotent_bound_to_approval_and_survive_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                recipe_sha256 = "1" * 64
                dag_sha256 = "2" * 64
                output_sha256 = "3" * 64
                store.start_video_attempt(
                    "checkpoint-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-42", recipe_sha256=recipe_sha256,
                )
                store.mark_video_attempt_running("checkpoint-attempt")
                first = store.record_video_stage_checkpoint(
                    "checkpoint-attempt", worker_id="status-api", lease_epoch=1,
                    batch_id="batch_001", batch_fingerprint="d" * 64,
                    step_id="preflight", stage_id="preflight",
                    dag_sha256=dag_sha256, checkpoint_key="video-studio:checkpoint-attempt:dag:001",
                    input_sha256="a" * 64, output_sha256=output_sha256,
                    metadata={"artifact": "preflight.json"},
                )
                repeated = store.record_video_stage_checkpoint(
                    "checkpoint-attempt", worker_id="status-api", lease_epoch=1,
                    batch_id="batch_001", batch_fingerprint="d" * 64,
                    step_id="preflight", stage_id="preflight",
                    dag_sha256=dag_sha256, checkpoint_key="video-studio:checkpoint-attempt:dag:001",
                    input_sha256="a" * 64, output_sha256=output_sha256,
                    metadata={"artifact": "preflight.json"},
                )
                self.assertEqual(first, repeated)
                self.assertEqual(first["recipe_sha256"], recipe_sha256)
                self.assertEqual(first["approval_task_id"], "commander-approval-42")
                self.assertEqual(
                    [item["stage_id"] for item in store.list_video_stage_checkpoints(
                        "checkpoint-attempt", worker_id="status-api", lease_epoch=1, dag_sha256=dag_sha256,
                    )],
                    ["preflight"],
                )
                with self.assertRaisesRegex(ValueError, "idempotency key conflicts"):
                    store.record_video_stage_checkpoint(
                        "checkpoint-attempt", worker_id="status-api", lease_epoch=1,
                        batch_id="batch_001", batch_fingerprint="d" * 64,
                        step_id="preflight", stage_id="preflight",
                        dag_sha256=dag_sha256, checkpoint_key="video-studio:checkpoint-attempt:dag:001",
                        input_sha256="a" * 64, output_sha256="4" * 64,
                        metadata={"artifact": "different.json"},
                    )
                with self.assertRaisesRegex(ValueError, "current owning worker"):
                    store.record_video_stage_checkpoint(
                        "checkpoint-attempt", worker_id="stale-worker", lease_epoch=1,
                        batch_id="batch_001", batch_fingerprint="d" * 64,
                        step_id="preflight", stage_id="preflight",
                        dag_sha256=dag_sha256, checkpoint_key="video-studio:checkpoint-attempt:dag:001",
                        input_sha256="b" * 64, output_sha256="5" * 64,
                    )
                store.fail_video_attempt("checkpoint-attempt", failure_code="video.test_failure")
                store.retry_failed_video_attempt("checkpoint-attempt", max_attempts=2)
                store.claim_next_video_attempt("checkpoint-worker-2", lease_seconds=120)
                resumed = store.list_video_stage_checkpoints(
                    "checkpoint-attempt", worker_id="checkpoint-worker-2", lease_epoch=2, dag_sha256=dag_sha256,
                )
                self.assertEqual([item["stage_id"] for item in resumed], ["preflight"])
                self.assertEqual(resumed[0]["output_sha256"], output_sha256)
                self.assertEqual(resumed[0]["lease_epoch"], 1)
                with self.assertRaisesRegex(ValueError, "lease epoch"):
                    store.record_video_stage_checkpoint(
                        "checkpoint-attempt", worker_id="checkpoint-worker-2", lease_epoch=1,
                        batch_id="batch_001", batch_fingerprint="d" * 64,
                        step_id="preflight", stage_id="preflight",
                        dag_sha256=dag_sha256, checkpoint_key="video-studio:checkpoint-attempt:dag:001",
                        input_sha256="b" * 64, output_sha256="5" * 64,
                    )
                next_epoch = store.record_video_stage_checkpoint(
                    "checkpoint-attempt", worker_id="checkpoint-worker-2", lease_epoch=2,
                    batch_id="batch_001", batch_fingerprint="d" * 64,
                    step_id="preflight", stage_id="preflight",
                    dag_sha256=dag_sha256, checkpoint_key="video-studio:checkpoint-attempt:dag:001",
                    input_sha256="b" * 64, output_sha256="5" * 64,
                )
                self.assertEqual(next_epoch["lease_epoch"], 2)
                self.assertEqual(len(store.list_video_stage_checkpoints(
                    "checkpoint-attempt", worker_id="checkpoint-worker-2", lease_epoch=2, dag_sha256=dag_sha256,
                )), 2)
                self.assertEqual(store.list_video_stage_checkpoints(
                    "checkpoint-attempt", worker_id="checkpoint-worker-2", lease_epoch=2, dag_sha256="6" * 64,
                ), [])
                store.connection.execute(
                    "UPDATE initiative_video_attempts SET recipe_sha256=? WHERE attempt_id=?",
                    ("7" * 64, "checkpoint-attempt"),
                )
                store.connection.commit()
                self.assertEqual(store.list_video_stage_checkpoints(
                    "checkpoint-attempt", worker_id="checkpoint-worker-2", lease_epoch=2, dag_sha256=dag_sha256,
                ), [])
                store.connection.execute(
                    "UPDATE initiative_video_attempts SET recipe_sha256=?,approval_task_id=? WHERE attempt_id=?",
                    (recipe_sha256, "commander-approval-replaced", "checkpoint-attempt"),
                )
                store.connection.commit()
                self.assertEqual(store.list_video_stage_checkpoints(
                    "checkpoint-attempt", worker_id="checkpoint-worker-2", lease_epoch=2, dag_sha256=dag_sha256,
                ), [])

    def test_video_stage_checkpoint_reads_require_current_lease_and_can_filter_history(self):
        import time

        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    "lease-read-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-read", recipe_sha256="1" * 64,
                )
                store.claim_next_video_attempt("same-worker", lease_seconds=120)
                store.record_video_stage_checkpoint(
                    "lease-read-attempt", worker_id="same-worker", lease_epoch=1,
                    batch_id="batch_one", batch_fingerprint="2" * 64,
                    step_id="shot_001", stage_id="motion_design",
                    dag_sha256="3" * 64,
                    checkpoint_key="video-studio:lease-read-attempt:dag:001",
                    input_sha256="4" * 64, output_sha256="5" * 64,
                )
                store.fail_video_attempt("lease-read-attempt", failure_code="video.retry")
                store.retry_failed_video_attempt("lease-read-attempt", max_attempts=2)
                store.claim_next_video_attempt("same-worker", lease_seconds=120)
                history = store.list_video_stage_checkpoints(
                    "lease-read-attempt", worker_id="same-worker", lease_epoch=2,
                    dag_sha256="3" * 64, batch_id="batch_one", step_id="shot_001",
                )
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["lease_epoch"], 1)
                self.assertEqual(store.list_video_stage_checkpoints(
                    "lease-read-attempt", worker_id="same-worker", lease_epoch=2,
                    dag_sha256="3" * 64, batch_id="batch_other",
                ), [])
                with self.assertRaisesRegex(ValueError, "current owning worker"):
                    store.list_video_stage_checkpoints(
                        "lease-read-attempt", worker_id="stale-worker", lease_epoch=2, dag_sha256="3" * 64,
                    )
                with self.assertRaisesRegex(ValueError, "current owning worker"):
                    store.list_video_stage_checkpoints(
                        "lease-read-attempt", worker_id="same-worker", lease_epoch=1, dag_sha256="3" * 64,
                    )
                store.connection.execute(
                    "UPDATE initiative_video_attempts SET lease_expires_at=? WHERE attempt_id=?",
                    (time.time() - 1, "lease-read-attempt"),
                )
                store.connection.commit()
                with self.assertRaisesRegex(ValueError, "unexpired lease epoch"):
                    store.list_video_stage_checkpoints(
                        "lease-read-attempt", worker_id="same-worker", lease_epoch=2, dag_sha256="3" * 64,
                    )

    def test_video_attempt_lease_renewal_preserves_epoch_and_rejects_stale_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("lease-renew-attempt", mode="prompt_to_video")
                claimed = store.claim_next_video_attempt("lease-worker", lease_seconds=60, now=1000)
                self.assertEqual(claimed["attempt_count"], 1)
                self.assertEqual(claimed["lease_expires_at"], 1060)
                renewed = store.renew_video_attempt_lease(
                    "lease-renew-attempt", worker_id="lease-worker", lease_epoch=1,
                    lease_seconds=120, now=1010,
                )
                self.assertEqual(renewed["attempt_count"], 1)
                self.assertEqual(renewed["lease_expires_at"], 1130)
                unchanged = store.renew_video_attempt_lease(
                    "lease-renew-attempt", worker_id="lease-worker", lease_epoch=1,
                    lease_seconds=15, now=1020,
                )
                self.assertEqual(unchanged["lease_expires_at"], 1130)
                for worker_id, epoch, timestamp in (
                    ("stale-worker", 1, 1030), ("lease-worker", 2, 1030),
                    ("lease-worker", 1, 1130),
                ):
                    with self.subTest(worker_id=worker_id, epoch=epoch, timestamp=timestamp):
                        with self.assertRaisesRegex(ValueError, "unexpired lease epoch"):
                            store.renew_video_attempt_lease(
                                "lease-renew-attempt", worker_id=worker_id, lease_epoch=epoch,
                                lease_seconds=60, now=timestamp,
                            )
                self.assertEqual(store.get_video_attempt("lease-renew-attempt")["lease_expires_at"], 1130)

    def test_video_stage_checkpoints_require_approval_task_and_recipe_bound_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt("unbound-attempt", mode="prompt_to_video")
                store.mark_video_attempt_running("unbound-attempt")
                with self.assertRaisesRegex(ValueError, "approval task"):
                    store.record_video_stage_checkpoint(
                        "unbound-attempt", worker_id="status-api", lease_epoch=1,
                        batch_id="batch_001", batch_fingerprint="d" * 64,
                        step_id="preflight", stage_id="preflight",
                        dag_sha256="2" * 64, checkpoint_key="video-studio:unbound-attempt:dag:001",
                        input_sha256="a" * 64, output_sha256="3" * 64,
                    )

    def test_video_stage_checkpoint_migration_preserves_legacy_attempt_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "initiatives.sqlite3"
            with InitiativeStore(database) as store:
                store.start_video_attempt("legacy-checkpoint-attempt", mode="prompt_to_video")
                before_attempt = store.get_video_attempt("legacy-checkpoint-attempt")
                before_events = store.list_video_attempt_events("legacy-checkpoint-attempt")
                store.connection.execute("DROP TRIGGER initiative_video_stage_checkpoints_no_update")
                store.connection.execute("DROP TRIGGER initiative_video_stage_checkpoints_no_delete")
                store.connection.execute("DROP INDEX idx_video_stage_checkpoints_timeline")
                store.connection.execute("DROP TABLE initiative_video_stage_checkpoints")
                store.connection.commit()
            with InitiativeStore(database) as reopened:
                self.assertEqual(reopened.get_video_attempt("legacy-checkpoint-attempt"), before_attempt)
                self.assertEqual(reopened.list_video_attempt_events("legacy-checkpoint-attempt"), before_events)
                columns = {row["name"] for row in reopened.connection.execute(
                    "PRAGMA table_info(initiative_video_stage_checkpoints)"
                )}
                self.assertIn("output_sha256", columns)
                triggers = {row["name"] for row in reopened.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )}
                self.assertIn("initiative_video_stage_checkpoints_no_update", triggers)
                self.assertIn("initiative_video_stage_checkpoints_no_delete", triggers)

    def test_video_stage_checkpoint_migrates_stage_only_rows_to_legacy_batch_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "initiatives.sqlite3"
            with InitiativeStore(database) as store:
                store.start_video_attempt(
                    "stage-only-legacy-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-legacy", recipe_sha256="1" * 64,
                )
                store.mark_video_attempt_running("stage-only-legacy-attempt")
                store.connection.execute("DROP TRIGGER initiative_video_stage_checkpoints_no_update")
                store.connection.execute("DROP TRIGGER initiative_video_stage_checkpoints_no_delete")
                store.connection.execute("DROP INDEX idx_video_stage_checkpoints_timeline")
                store.connection.execute("DROP TABLE initiative_video_stage_checkpoints")
                store.connection.execute("""CREATE TABLE initiative_video_stage_checkpoints(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id TEXT NOT NULL REFERENCES initiative_video_attempts(attempt_id) ON DELETE RESTRICT,
                    dag_sha256 TEXT NOT NULL CHECK(length(dag_sha256)=64),
                    checkpoint_key TEXT NOT NULL, stage_id TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL CHECK(length(input_sha256)=64),
                    output_sha256 TEXT NOT NULL CHECK(length(output_sha256)=64),
                    recipe_sha256 TEXT NOT NULL CHECK(length(recipe_sha256)=64),
                    approval_task_id TEXT NOT NULL, worker_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                    UNIQUE(attempt_id,dag_sha256,stage_id)
                )""")
                store.connection.execute(
                    """INSERT INTO initiative_video_stage_checkpoints
                    (attempt_id,dag_sha256,checkpoint_key,stage_id,input_sha256,output_sha256,
                     recipe_sha256,approval_task_id,worker_id,metadata_json,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    ("stage-only-legacy-attempt", "2" * 64,
                     "video-studio:stage-only-legacy-attempt:dag:001", "preflight",
                     "3" * 64, "4" * 64, "1" * 64, "commander-approval-legacy",
                     "status-api", '{"legacy":true}', "2026-01-01T00:00:00+00:00"),
                )
                store.connection.commit()
            with InitiativeStore(database) as migrated:
                rows = migrated.list_video_stage_checkpoints(
                    "stage-only-legacy-attempt", worker_id="status-api", lease_epoch=1, dag_sha256="2" * 64,
                )
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["batch_id"], "legacy")
                self.assertEqual(rows[0]["batch_fingerprint"], "2" * 64)
                self.assertEqual(rows[0]["step_id"], "preflight")
                self.assertEqual(rows[0]["lease_epoch"], 1)
                self.assertEqual(rows[0]["metadata"], {"legacy": True})

    def test_video_stage_checkpoint_rejects_expired_worker_lease(self):
        import time

        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    "expired-checkpoint-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-expired", recipe_sha256="1" * 64,
                )
                store.claim_next_video_attempt("lease-worker", lease_seconds=120)
                store.connection.execute(
                    "UPDATE initiative_video_attempts SET lease_expires_at=? WHERE attempt_id=?",
                    (time.time() - 1, "expired-checkpoint-attempt"),
                )
                store.connection.commit()
                with self.assertRaisesRegex(ValueError, "unexpired lease"):
                    store.record_video_stage_checkpoint(
                        "expired-checkpoint-attempt", worker_id="lease-worker", lease_epoch=1,
                        batch_id="batch_expired", batch_fingerprint="e" * 64,
                        step_id="preflight", stage_id="preflight",
                        dag_sha256="2" * 64,
                        checkpoint_key="video-studio:expired-checkpoint-attempt:dag:001",
                        input_sha256="3" * 64, output_sha256="4" * 64,
                    )

    def test_video_stage_checkpoint_rows_are_append_only_and_foreign_key_constrained(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            with InitiativeStore(Path(directory) / "initiatives.sqlite3") as store:
                store.start_video_attempt(
                    "immutable-checkpoint-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-immutable", recipe_sha256="1" * 64,
                )
                store.mark_video_attempt_running("immutable-checkpoint-attempt")
                checkpoint = store.record_video_stage_checkpoint(
                    "immutable-checkpoint-attempt", worker_id="status-api", lease_epoch=1,
                    batch_id="batch_immutable", batch_fingerprint="f" * 64,
                    step_id="preflight", stage_id="preflight",
                    dag_sha256="2" * 64,
                    checkpoint_key="video-studio:immutable-checkpoint-attempt:dag:001",
                    input_sha256="3" * 64, output_sha256="4" * 64,
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(
                        "UPDATE initiative_video_stage_checkpoints SET output_sha256=? WHERE sequence=?",
                        ("5" * 64, checkpoint["sequence"]),
                    )
                store.connection.rollback()
                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(
                        "DELETE FROM initiative_video_stage_checkpoints WHERE sequence=?",
                        (checkpoint["sequence"],),
                    )
                store.connection.rollback()
                with self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(
                        """INSERT INTO initiative_video_stage_checkpoints
                        (attempt_id,dag_sha256,batch_id,batch_fingerprint,step_id,stage_id,lease_epoch,
                         checkpoint_key,input_sha256,output_sha256,recipe_sha256,approval_task_id,
                         worker_id,metadata_json,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("missing-parent", "6" * 64, "batch_missing", "6" * 64, "preflight",
                         "preflight", 1, "video-studio:missing-parent:dag:001", "7" * 64,
                         "8" * 64, "9" * 64, "commander-approval-missing", "worker", "{}",
                         "2026-01-01T00:00:00+00:00"),
                    )
                store.connection.rollback()
                self.assertEqual(len(store.list_video_stage_checkpoints(
                    "immutable-checkpoint-attempt", worker_id="status-api", lease_epoch=1, dag_sha256="2" * 64,
                )), 1)

    def test_video_stage_checkpoint_concurrent_identical_writes_create_one_row(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "initiatives.sqlite3"
            with InitiativeStore(database) as store:
                store.start_video_attempt(
                    "concurrent-checkpoint-attempt", mode="prompt_to_video",
                    approval_task_id="commander-approval-concurrent", recipe_sha256="1" * 64,
                )
                store.mark_video_attempt_running("concurrent-checkpoint-attempt")
            barrier = Barrier(2)

            def write_checkpoint():
                with InitiativeStore(database) as store:
                    barrier.wait(timeout=5)
                    return store.record_video_stage_checkpoint(
                        "concurrent-checkpoint-attempt", worker_id="status-api", lease_epoch=1,
                        batch_id="batch_concurrent", batch_fingerprint="a" * 64,
                        step_id="shot_001", stage_id="motion_design",
                        dag_sha256="2" * 64,
                        checkpoint_key="video-studio:concurrent-checkpoint-attempt:dag:001",
                        input_sha256="3" * 64, output_sha256="4" * 64,
                        metadata={"artifact": "same.json"},
                    )

            with ThreadPoolExecutor(max_workers=2) as executor:
                first, second = list(executor.map(lambda _index: write_checkpoint(), range(2)))
            self.assertEqual(first["sequence"], second["sequence"])
            self.assertEqual(first["batch_id"], "batch_concurrent")
            self.assertEqual(first["batch_fingerprint"], "a" * 64)
            self.assertEqual(first["step_id"], "shot_001")
            self.assertEqual(first["stage_id"], "motion_design")
            self.assertEqual(first["lease_epoch"], 1)
            with InitiativeStore(database) as store:
                self.assertEqual(store.connection.execute(
                    "SELECT COUNT(*) FROM initiative_video_stage_checkpoints"
                ).fetchone()[0], 1)

if __name__ == "__main__":
    unittest.main()
