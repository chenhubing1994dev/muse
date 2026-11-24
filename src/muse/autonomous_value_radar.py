"""Daily evidence-aware value radar for Muse's existing Commander routines.

Ranks existing local research reports and writes a local digest. It never
invents customer demand or revenue, spends, contacts customers, publishes,
dispatches GPU work, or approves its own ideas.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from muse.routines import RoutineExecutionContext, routine_handler_with_context
from muse.value_forge import ValueForge

ROUTINE_NAME = "daily-value-radar"
ROUTINE_KIND = "autonomous_value_radar"
ROUTINE_INTERVAL_SECONDS = 24 * 60 * 60
MAX_REPORTS = 20
MAX_CANDIDATES = 24


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_date(value: date | str | None) -> date:
    if value is None:
        return datetime.now(timezone.utc).date()
    if isinstance(value, datetime):
        raise TypeError("as_of must be a date, not datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("as_of must be an ISO date") from exc
    raise TypeError("as_of must be a date or ISO date string")


def build_daily_value_radar(
    reports: Sequence[Mapping[str, Any]],
    *,
    as_of: date | str | None = None,
    max_candidates: int = MAX_CANDIDATES,
) -> dict[str, Any]:
    """Rank research-derived value hypotheses without treating them as market facts.

    Each input is a mapping with exactly report_path and report. Reports are
    already acquired local artifacts; this function performs no I/O.
    """
    if not isinstance(reports, Sequence) or isinstance(reports, (str, bytes)):
        raise TypeError("reports must be a sequence")
    if len(reports) > MAX_REPORTS:
        raise ValueError(f"reports cannot exceed {MAX_REPORTS}")
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= MAX_CANDIDATES:
        raise ValueError(f"max_candidates must be between 1 and {MAX_CANDIDATES}")

    scan_date = _as_date(as_of)
    forge = ValueForge()
    candidates: list[dict[str, Any]] = []
    seen_candidate_ids: set[str] = set()
    report_count = source_count = experiment_count = counter_evidence_count = 0

    for item in reports:
        if not isinstance(item, Mapping) or set(item) != {"report_path", "report"}:
            raise ValueError("each report entry must contain exactly report_path and report")
        report_path = item["report_path"]
        report = item["report"]
        if not isinstance(report_path, str) or not report_path.strip() or len(report_path) > 2048:
            raise ValueError("report_path must be non-empty text of at most 2048 characters")
        if any(ord(char) < 32 for char in report_path):
            raise ValueError("report_path contains control characters")
        if not isinstance(report, Mapping):
            raise TypeError("report must be a mapping")

        report_count += 1
        digest = _canonical_digest(report)
        evaluation = forge.evaluate_research_report(report, report_path=report_path.strip())
        source_count += evaluation["source_count"]
        experiment_count += evaluation["experiment_count"]
        counter_evidence_count += evaluation["counter_evidence_count"]
        synthesis = report.get("synthesis")
        synthesis = synthesis if isinstance(synthesis, Mapping) else {}

        for ranked in evaluation["candidates"]:
            pattern_id = ranked["pattern_id"]
            candidate_id = _canonical_digest({
                "report_path": report_path.strip(),
                "report_sha256": digest,
                "pattern_id": pattern_id,
            })[:24]
            if candidate_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate_id)
            if pattern_id == "research_to_video":
                next_step = "prepare_cited_content_sample"
                autonomous_actions = [
                    "draft_script_and_storyboard",
                    "match_licensed_or_user_supplied_assets",
                    "prepare_narration_and_offline_preview",
                    "run_content_quality_checks",
                ]
            else:
                next_step = "design_a_small_reproducible_experiment"
                autonomous_actions = [
                    "write_hypothesis_and_falsifier",
                    "prepare_local_test_fixture",
                    "record_counter_evidence_and_uncertainty",
                ]
            candidates.append({
                "candidate_id": candidate_id,
                "pattern_id": pattern_id,
                "title": ranked["title"],
                "heuristic_score": ranked["evaluation"]["score"],
                "evidence_state": "hypothesis_only",
                "report_path": report_path.strip(),
                "report_sha256": digest,
                "source_refs": ranked["evidence_refs"],
                "source_count": evaluation["source_count"],
                "experiment_count": evaluation["experiment_count"],
                "counter_evidence_count": evaluation["counter_evidence_count"],
                "research_summary": str(synthesis.get("executive_summary") or "")[:1200],
                "next_step": next_step,
                "autonomous_actions": autonomous_actions,
                "requires_human_review": True,
                "external_actions_allowed": False,
                "revenue_forecast": None,
            })

    candidates.sort(
        key=lambda row: (-row["heuristic_score"], -row["source_count"], row["candidate_id"])
    )
    candidates = candidates[:max_candidates]
    cycle_id = _canonical_digest({
        "as_of": scan_date.isoformat(),
        "candidate_ids": [row["candidate_id"] for row in candidates],
        "report_count": report_count,
    })[:24]
    return {
        "schema_version": 1,
        "cycle_id": cycle_id,
        "as_of": scan_date.isoformat(),
        "status": "ranked_hypotheses" if candidates else "no_new_opportunities",
        "mission": "每天寻找一个低成本、可验证、可交付的价值创造机会",
        "summary": {
            "reports_scanned": report_count,
            "source_references": source_count,
            "experiments_found": experiment_count,
            "counter_evidence_items": counter_evidence_count,
            "candidates": len(candidates),
        },
        "candidates": candidates,
        "autonomy_policy": {
            "may_research_and_draft": True,
            "may_build_local_reversible_prototypes": True,
            "may_prepare_publication_package": True,
            "may_publish_or_contact_customers": False,
            "may_spend_or_transfer_funds": False,
            "may_create_external_accounts_or_wallets": False,
            "may_bypass_platform_restrictions_or_shutdown": False,
            "approval_required_for_external_effects": True,
        },
        "interpretation": (
            "Scores rank research hypotheses only. They are not probabilities, "
            "proof of demand, willingness-to-pay, profit, or revenue forecasts."
        ),
    }


def write_daily_value_radar(workspace: str | Path, report: Mapping[str, Any]) -> Path:
    """Atomically persist one deterministic local digest under reports/value-radar."""
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise FileNotFoundError("workspace must be an existing directory")
    parent = root / "reports"
    target_dir = parent / "value-radar"
    for path in (parent, target_dir):
        if path.is_symlink():
            raise ValueError("value-radar output path cannot contain symlinks")
    parent.mkdir(exist_ok=True)
    target_dir.mkdir(exist_ok=True)
    resolved_dir = target_dir.resolve()
    if resolved_dir.parent != parent.resolve():
        raise ValueError("value-radar output directory escaped the workspace")
    cycle_id = report.get("cycle_id")
    as_of = report.get("as_of")
    if not isinstance(cycle_id, str) or len(cycle_id) != 24:
        raise ValueError("report has an invalid cycle_id")
    if not isinstance(as_of, str):
        raise ValueError("report has an invalid as_of date")
    try:
        date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError("report has an invalid as_of date") from exc
    destination = resolved_dir / f"{as_of}-{cycle_id}.json"
    if destination.exists():
        if destination.is_symlink():
            raise ValueError("value-radar report cannot be a symlink")
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if existing != dict(report):
            raise FileExistsError("existing value-radar report conflicts with this cycle")
        return destination

    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n",
        prefix=".value-radar-", suffix=".tmp", dir=resolved_dir, delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if destination.exists():
            raise FileExistsError("value-radar destination appeared during write")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


def make_value_radar_routine_handler(
    *,
    workspace: str | Path,
    report_loader: Callable[[int], Sequence[Mapping[str, Any]]],
) -> Callable[..., dict[str, Any]]:
    """Create a RoutineStore handler; the Commander runtime registers it.

    report_loader must return authorized local reports and must not perform
    network access, spend money, publish, or mutate external systems.
    """
    if not callable(report_loader):
        raise TypeError("report_loader must be callable")

    def handle(
        payload: dict[str, Any],
        *,
        execution_context: RoutineExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - {"max_reports", "max_candidates"}:
            raise ValueError("value-radar routine payload contains unsupported fields")
        max_reports = payload.get("max_reports", MAX_REPORTS)
        max_candidates = payload.get("max_candidates", MAX_CANDIDATES)
        if isinstance(max_reports, bool) or not isinstance(max_reports, int) or not 1 <= max_reports <= MAX_REPORTS:
            raise ValueError(f"max_reports must be between 1 and {MAX_REPORTS}")
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= MAX_CANDIDATES:
            raise ValueError(f"max_candidates must be between 1 and {MAX_CANDIDATES}")
        execution_context.raise_if_cancelled()
        reports = report_loader(max_reports)
        execution_context.raise_if_cancelled()
        if not isinstance(reports, Sequence) or isinstance(reports, (str, bytes)) or len(reports) > max_reports:
            raise ValueError("report_loader returned an invalid or oversized report batch")
        radar = build_daily_value_radar(reports, max_candidates=max_candidates)
        execution_context.raise_if_cancelled()
        path = write_daily_value_radar(workspace, radar)
        return {
            "status": radar["status"],
            "cycle_id": radar["cycle_id"],
            "report_path": str(path.relative_to(Path(workspace).resolve())),
            "candidate_count": radar["summary"]["candidates"],
            "external_actions_performed": False,
            "approval_required": True,
        }

    return routine_handler_with_context(handle)
