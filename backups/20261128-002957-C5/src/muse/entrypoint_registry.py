"""Entry-point registry (backlog A): every way work can start in Muse is enumerated and classified.

The diagnostic probe L4.12 enumerates the *actual* entry points from source (status_api POST blocks,
control-center POST routes, Commander/Omega prefixes, autonomous routines, worker handlers, the
video completion hook, console scripts and ``python -m`` modules) and fails when

* an actual entry point is missing here (new entries cannot slip in unclassified), or a registered
  one no longer exists (stale registry);
* an entry declared ``commander_ticket`` / ``approval_gated`` / ``kill_switch_gated`` /
  ``legacy_ticket`` lacks the matching evidence in its source segment;
* the number of ``debt`` entries exceeds :data:`DEBT_CEILING` (it may only go down).

Classes
-------
commander_ticket   runs inside a MuseCommander ticket (approval gate, review, receipt, cost ledger)
approval_gated     acts only after an explicit human decision / confirmation phrase (pre-Commander gate)
legacy_ticket      executes TaskQueue tickets claimed by the worker (same SSOT, no MuseCommander receipt)
kill_switch_gated  automated housekeeping that at least obeys the global Commander gate
safety_stop        stop/pause actions that only reduce activity: always allowed, audited, never start work
guard_only         request validation shared by several routes; performs no action itself
user_cli           a command a human types locally (direct user action; not an agent decision)
debt               writes / external work without any of the above — migration backlog, honestly listed
"""
from __future__ import annotations

CLASSES = ("commander_ticket", "approval_gated", "legacy_ticket", "kill_switch_gated", "safety_stop", "guard_only",
           "user_cli", "debt")

# status_api do_POST blocks, keyed by the exact (stripped) source line that opens the block.
STATUS_API_POST_BLOCKS: dict[str, tuple[str, str]] = {
    'if parsed.path.startswith("/api/v1/omega/"):': ("debt", "delegates to omega_api (evolution rollback, safety control, uploads) — not yet per-ticket"),
    'if parsed.path.startswith("/api/v1/commander/"):': ("commander_ticket", "commander_api: approve/cancel/kill on MuseCommander"),
    'if parsed.path.startswith("/api/v1/control/"):': ("debt", "delegates to control_center_api; xuanshu write is Commander-routed, task submit/decide use TaskQueue approval"),
    'if decide_match or parsed.path == "/api/v1/reviewer-key/initialize":': ("approval_gated", "SIGN-REVIEW / INIT-REVIEWER-KEY confirmation phrases"),
    'if parsed.path == "/api/v1/plugins/discover" or parsed.path.startswith("/api/v1/plugins/"):': ("approval_gated", "plugin approve/enable require explicit human calls"),
    'if parsed.path.startswith("/api/v1/companion/"):': ("approval_gated", "pairing approve/deny/revoke are explicit human decisions"),
    'if parsed.path == "/api/v1/fusion/submit" or re.fullmatch(r"/api/v1/fusion/jobs/[a-f0-9]{32}/(?:approve|rollback)", parsed.path):': ("approval_gated", "FUSE-CAPABILITIES confirmation"),
    "if parsed.path in DAEMON_CONTROL_ROUTES:": ("guard_only", "Content-Length validation for daemon routes"),
    "if parsed.path in DAEMON_SAFETY_ROUTES:": ("safety_stop", "daemon stop/pause, audited to safety_actions.jsonl"),
    "if parsed.path in DAEMON_START_ROUTES:": ("commander_ticket", "C4: daemon start/resume/once via submit_and_wait"),
    "if parsed.path in {": ("debt", "studio search-media/generate-script/render/philosophy-inject — renders / model calls"),
    "if visual_review_match:": ("debt", "visual review ledger write"),
    "if subtitle_approval_match:": ("approval_gated", "human subtitle approval"),
    'if parsed.path == "/api/v1/pipelines/submit":': ("debt", "pipeline controller submit"),
    "if initiative_performance_match:": ("debt", "initiative performance write"),
    "if initiative_feedback_match:": ("debt", "initiative feedback write"),
    'if parsed.path == "/api/v1/video-approval-requests":': ("debt", "creates video approval requests"),
    "if approval_match:": ("approval_gated", "video approval request approve"),
    'if parsed.path == "/api/v1/video-products":': ("debt", "video product build"),
    'if parsed.path == "/api/v1/radar/scan":': ("debt", "capability/value radar scan"),
    'if parsed.path == "/api/v1/inspirations/evolve":': ("commander_ticket", "T2 commander_routes.submit_and_wait"),
    'if parsed.path == "/api/v1/inspirations":': ("commander_ticket", "T2 commander_routes.submit_and_wait"),
    'if parsed.path == "/api/v1/galaxy/route-preview":': ("debt", "specialist route preview"),
    'if parsed.path == "/api/v1/metaphysics/yijing":': ("debt", "yijing inquiry"),
    'if parsed.path != "/api/v1/feedback":': ("debt", "structured feedback write (final fallback block)"),
}

CONTROL_POST_ROUTES: dict[str, tuple[str, str]] = {
    "/api/v1/control/tasks": ("approval_gated", "submit_task creates waiting_approval tickets"),
    "/api/v1/control/tasks/({_TASK_ID})/(cancel|retry)": ("approval_gated", "explicit operator action with confirmation"),
    "/api/v1/control/approvals/({_TASK_ID})/decide": ("approval_gated", "APPROVE-TICKET confirmation"),
    "/api/v1/control/memory/candidates/([0-9]{1,12})/decide": ("approval_gated", "human memory candidate decision"),
    "/api/v1/control/xuanshu/hypotheses": ("commander_ticket", "T2 write_to_wiki via submit_and_wait"),
    "/api/v1/control/xuanshu/dispatch": ("approval_gated", "SUBMIT-TO-COMMANDER creates waiting_approval ticket"),
    "/api/v1/control/creation/feedback": ("approval_gated", "LINK-FEEDBACK-TO-CLAIM confirmation"),
}

ROUTINES: dict[str, tuple[str, str]] = {name: ("kill_switch_gated", "AutonomousSupervisor.run_tick checks the global Commander gate")
                                        for name in ("watchdog_check", "knowledge_lint", "capability_scan", "queue_health",
                                                     "autonomous_research", "video_worker", "autonomous_value_radar")}

WORKER_HANDLERS: dict[str, tuple[str, str]] = {
    name: ("legacy_ticket", "worker.run_once claims TaskQueue tickets (approval-respecting SSOT)")
    for name in ("echo", "approved_plan_intake", "trigger_work_proposal", "review_followup_research",
                 "local_workflow", "inspiration_evolution")
}

HOOKS: dict[str, tuple[str, str]] = {
    "video_task_worker.trigger_publish_bundle_after_video": ("commander_ticket", "T4 Workflow C plan per video package"),
}

# Current honest debt count, counted per source block. It may only be lowered when debt is migrated.
# History: 14 blocks (C3) → 13 (C4: daemon start/resume/once moved to Commander tickets).
DEBT_CEILING = 13


# Console scripts (pyproject) and `python -m` modules: direct local user commands (class user_cli).
CONSOLE_SCRIPTS: frozenset[str] = frozenset({
    "muse-agent-status=muse.herdr_adapter:main",
    "muse-agents=muse.agent_discovery:main",
    "muse-autonomous=muse.autonomous_runtime:main",
    "muse-autonomy=muse.autonomy:main",
    "muse-bridge=muse.chatgpt_bridge:main",
    "muse-business=muse.business_opportunities:main",
    "muse-candidate-enqueue=muse.candidate_enqueuer:main",
    "muse-capabilities=muse.capability_cli:main",
    "muse-chat=muse.chat_cli:main",
    "muse-chatanywhere-smoke=muse.chatanywhere_smoke:main",
    "muse-chimera-voice=muse.chimera_vocal:main",
    "muse-companion-host=muse.companion_transport:main",
    "muse-consciousness=muse.consciousness_engine:main",
    "muse-desktop=muse.desktop_adapter:main",
    "muse-evolution=muse.evolution_guard:main",
    "muse-feedback=muse.feedback_cli:main",
    "muse-ffmpeg-skill=muse.ffmpeg_skill:main",
    "muse-film=muse.cinematic_factory:main",
    "muse-herdr-core-smoke=muse.herdr_core_smoke:main",
    "muse-idle-watch=muse.idle_watch:main",
    "muse-improve=muse.improvement_cli:main",
    "muse-inspire=muse.inspiration:main",
    "muse-intake=muse.intake_runtime:main",
    "muse-intent=muse.intent_cli:main",
    "muse-media=muse.media:main",
    "muse-memory=muse.memory_cli:main",
    "muse-mind=muse.consciousness_loop:main",
    "muse-model-supervisor=muse.model_supervisor:main",
    "muse-open-design=muse.open_design:main",
    "muse-philosophy=muse.philosophy:main",
    "muse-piper-tts=muse.piper_tts:main",
    "muse-plan-hour=muse.workload_cli:main",
    "muse-plugins=muse.plugin_platform:main",
    "muse-portal-admin=muse.web_portal_admin:main",
    "muse-portal-db=muse.portal_maintenance:main",
    "muse-product=muse.product_builder:main",
    "muse-progress=muse.progress_cli:main",
    "muse-prompt=muse.prompt_engine:main",
    "muse-prototype=muse.prototype_builder:main",
    "muse-quotes=muse.quote_intelligence:main",
    "muse-radar=muse.capability_radar:main",
    "muse-recreate=muse.software_recreation:main",
    "muse-ref-repair=muse.inspiration_ref_repair:main",
    "muse-reflect=muse.memory_reflection:main",
    "muse-reminders=muse.reminders:main",
    "muse-research=muse.research:main",
    "muse-research-pipeline=muse.research_pipeline:main",
    "muse-research-read=muse.research_fetch:main",
    "muse-research-wiki-audit=muse.research_wiki_audit:main",
    "muse-research-wiki-citation=muse.research_wiki_citation:main",
    "muse-research-wiki-review=muse.research_wiki_review:main",
    "muse-research-wiki-review-record=muse.research_wiki_review_record:main",
    "muse-resume=muse.handoff:main",
    "muse-revenue-ledger=muse.revenue_ledger:main",
    "muse-reviewer-attest=muse.reviewer_attestation:main",
    "muse-routines=muse.routines:main",
    "muse-ruflo=muse.ruflo_adapter:main",
    "muse-runtime=muse.unified_runtime:main",
    "muse-signed-review=muse.signed_review:main",
    "muse-software-craft=muse.software_crafting:main",
    "muse-source-identity=muse.source_identity_cli:main",
    "muse-status-api=muse.status_api:main",
    "muse-task=muse.task_cli:main",
    "muse-test=muse.test_runner:main",
    "muse-value=muse.value_forge:main",
    "muse-video=muse.video_factory:main",
    "muse-video-clips=muse.video_highlights:main",
    "muse-video-editable-export=muse.video_editable_export:main",
    "muse-video-product=muse.video_product_builder:main",
    "muse-video-publish-pack=muse.video_publish_package:main",
    "muse-video-qa=muse.video_qa:main",
    "muse-video-recipe-trace=muse.video_recipe_trace:main",
    "muse-video-reviewer-signer=muse.video_reviewer_signer:main",
    "muse-video-studio=muse.video_studio:main",
    "muse-video-worker=muse.video_task_worker:main",
    "muse-watchdog=muse.watchdog:main",
    "muse-web-portal=muse.web_portal:main",
    "muse-worker=muse.worker:main",
})
MAIN_MODULES: frozenset[str] = frozenset({
    "muse.agent_discovery",
    "muse.autonomous_runtime",
    "muse.autonomy",
    "muse.candidate_enqueuer",
    "muse.cantonese_tts",
    "muse.capability_cli",
    "muse.capability_radar",
    "muse.chat_cli",
    "muse.chatanywhere_smoke",
    "muse.chatgpt_bridge",
    "muse.chimera_vocal",
    "muse.cinematic_factory",
    "muse.companion_transport",
    "muse.consciousness_engine",
    "muse.consciousness_loop",
    "muse.contextual_inquiry_cli",
    "muse.creative_studio_api",
    "muse.desktop_adapter",
    "muse.evolution_guard",
    "muse.feedback_cli",
    "muse.ffmpeg_skill",
    "muse.handoff",
    "muse.herdr_adapter",
    "muse.herdr_core_smoke",
    "muse.idle_watch",
    "muse.improvement_cli",
    "muse.inspiration",
    "muse.inspiration_ref_repair",
    "muse.intake_runtime",
    "muse.intent_cli",
    "muse.lease_recovery",
    "muse.local_dashboard",
    "muse.main",
    "muse.media",
    "muse.memory_cli",
    "muse.memory_reflection",
    "muse.model_supervisor",
    "muse.monetization_pilot",
    "muse.muse_commander",
    "muse.open_design",
    "muse.piper_tts",
    "muse.plugin_platform",
    "muse.portal_maintenance",
    "muse.product_builder",
    "muse.progress_cli",
    "muse.prompt_engine",
    "muse.prototype_builder",
    "muse.quote_intelligence",
    "muse.reminders",
    "muse.research",
    "muse.research_fetch",
    "muse.research_pipeline",
    "muse.research_wiki_audit",
    "muse.research_wiki_citation",
    "muse.research_wiki_review",
    "muse.research_wiki_review_record",
    "muse.revenue_ledger",
    "muse.reviewer_attestation",
    "muse.routines",
    "muse.ruflo_adapter",
    "muse.signed_review",
    "muse.software_crafting",
    "muse.software_recreation",
    "muse.source_identity_cli",
    "muse.status_api",
    "muse.task_cli",
    "muse.test_runner",
    "muse.unified_runtime",
    "muse.value_forge",
    "muse.video_editable_export",
    "muse.video_factory",
    "muse.video_highlights",
    "muse.video_motion_diagnostics",
    "muse.video_product_builder",
    "muse.video_prompt_studio",
    "muse.video_publish_package",
    "muse.video_qa",
    "muse.video_recipe_trace",
    "muse.video_reviewer_signer",
    "muse.video_studio",
    "muse.video_task_worker",
    "muse.watchdog",
    "muse.web_portal",
    "muse.web_portal_admin",
    "muse.worker",
    "muse.workflow_a",
    "muse.workflow_c",
    "muse.workload_cli",
})


def all_entries() -> dict[str, tuple[str, str]]:
    merged: dict[str, tuple[str, str]] = {}
    for prefix, table in (("http:status_api:", STATUS_API_POST_BLOCKS), ("http:control:", CONTROL_POST_ROUTES),
                          ("routine:", ROUTINES), ("worker:", WORKER_HANDLERS), ("hook:", HOOKS)):
        for key, value in table.items():
            merged[prefix + key] = value
    return merged
