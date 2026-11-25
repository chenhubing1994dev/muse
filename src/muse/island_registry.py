"""B398 island ledger: every formerly unreferenced module has an explicit, tested decision.

``wired``    — now has a production caller (named in ``via``).
``archived`` — kept as a tested library, deliberately *not* wired, with the reason; never deleted
               (core assets stay) and never imported by the runtime until a human flips the decision.
"""
from __future__ import annotations

from pathlib import Path

ISLANDS: dict[str, dict[str, str]] = {
    "jyutping": {"decision": "wired", "via": "cinematic_factory.align_subtitles (jyutping field validation)"},
    "mempalace_lifecycle": {"decision": "archived", "reason": "offline backup/restore tool; run manually before MemPalace upgrades"},
    "openrouter": {"decision": "archived", "reason": "cloud provider conflicts with the local/free-only policy (G-provider)"},
    "usage_observer": {"decision": "archived", "reason": "optional decorator; enable only when a provider needs usage metering"},
    "harness_evolution": {"decision": "archived", "reason": "superseded by evolution_guard signed-patch pipeline (B394)"},
    "lightpanda_adapter": {"decision": "archived", "reason": "optional CDP browser; requires a separately installed Lightpanda binary"},
    "celestial_calendar_provider": {"decision": "archived", "reason": "optional CalendarContext source for xuanshu providers; needs pinned engine"},
    "zhenyi_bazi_provider": {"decision": "archived", "reason": "optional symbolic lens; xuanshu_fusion accepts it as an injected provider"},
    "open_design_recovery": {"decision": "archived", "reason": "manual persistence-only recovery for interrupted Open Design runs"},
    "sandbox_file_manager": {"decision": "archived", "reason": "superseded by security_guards.safe_join/safe_extract_zip for Omega pages"},
    "social_content_kit": {"decision": "archived", "reason": "publishing step stays manual until the video factory release gate is signed"},
    "creator_media_plan": {"decision": "archived", "reason": "superseded by cinematic_factory.CinematicFactory.plan"},
    "prosodic_boundary": {"decision": "archived", "reason": "narration cut snapping requires reviewed phrase data not yet produced"},
}


def audit_islands(source_root: str | Path) -> dict[str, list[str]]:
    root = Path(source_root)
    missing = [name for name in ISLANDS if not (root / f"{name}.py").is_file()]
    undecided = [name for name, item in ISLANDS.items() if item.get("decision") not in {"wired", "archived"}
                 or not (item.get("via") or item.get("reason"))]
    wired_without_caller = []
    for name, item in ISLANDS.items():
        if item["decision"] != "wired":
            continue
        caller = item["via"].split(".", 1)[0]
        path = root / f"{caller}.py"
        if not path.is_file() or f"muse.{name}" not in path.read_text(encoding="utf-8"):
            wired_without_caller.append(name)
    return {"missing": missing, "undecided": undecided, "wired_without_caller": wired_without_caller}
