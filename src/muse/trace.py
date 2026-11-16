"""Evidence-linked causal tracing stored in the existing TaskQueue event log."""
from __future__ import annotations

import re
from typing import Any, Mapping

from muse.task_queue import TaskQueue


_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class TraceBlocked(RuntimeError):
    """A causal trace lacks enough evidence to be recorded or resolved."""


class FailureTraceRecorder:
    """Store competing hypotheses and evidence-backed resolutions on a failed Ticket."""

    def __init__(self, queue: TaskQueue) -> None:
        if not isinstance(queue, TaskQueue):
            raise TypeError("queue must be the existing TaskQueue")
        self.queue = queue

    def record_hypotheses(
        self, ticket_id: str, *, failure_code: str,
        hypotheses: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        task = self.queue.get(ticket_id)
        if task is None:
            raise KeyError(ticket_id)
        if task["status"] != "failed":
            raise TraceBlocked("causal hypotheses can only be attached to a failed Ticket")
        if not isinstance(failure_code, str) or not _FAILURE_CODE.fullmatch(failure_code) or len(failure_code) > 80:
            raise ValueError("failure_code must be a stable lowercase identifier")
        if not isinstance(hypotheses, tuple) or not 2 <= len(hypotheses) <= 5:
            raise ValueError("Trace requires 2-5 competing hypotheses")
        known_events = {event["event_id"] for event in self.queue.events(ticket_id)}
        clean = []
        ids = set()
        for item in hypotheses:
            if not isinstance(item, Mapping) or set(item) != {
                "hypothesis_id", "claim", "evidence_refs", "falsification_check"
            }:
                raise ValueError("each hypothesis has an invalid shape")
            hypothesis_id = item["hypothesis_id"]
            claim = item["claim"]
            refs = item["evidence_refs"]
            falsification = item["falsification_check"]
            if (not isinstance(hypothesis_id, str) or not re.fullmatch(r"h[1-5]", hypothesis_id)
                    or hypothesis_id in ids):
                raise ValueError("hypothesis IDs must be unique h1-h5 identifiers")
            if not isinstance(claim, str) or not claim.strip() or len(claim) > 500:
                raise ValueError("hypothesis claim must be text of at most 500 characters")
            if not isinstance(refs, tuple) or not 1 <= len(refs) <= 8 or any(
                not isinstance(ref, str) or ref not in known_events for ref in refs
            ):
                raise TraceBlocked("each hypothesis must cite 1-8 existing Ticket event IDs")
            if not isinstance(falsification, str) or not falsification.strip() or len(falsification) > 400:
                raise ValueError("falsification_check must be text of at most 400 characters")
            ids.add(hypothesis_id)
            clean.append({
                "hypothesis_id": hypothesis_id, "claim": claim.strip(),
                "evidence_refs": list(refs), "falsification_check": falsification.strip(),
                "resolution": "unresolved",
            })
        if len({item["claim"].casefold() for item in clean}) != len(clean):
            raise ValueError("competing hypotheses must be materially distinct")
        return self.queue.record_event(
            ticket_id, "failure.trace_hypotheses", actor="trace",
            payload={"failure_code": failure_code, "hypotheses": clean},
        )

    def resolve(
        self, ticket_id: str, *, hypothesis_id: str, disposition: str,
        evidence_refs: tuple[str, ...], rationale: str,
    ) -> dict[str, Any]:
        if disposition not in {"supported", "rejected", "inconclusive"}:
            raise ValueError("disposition must be supported, rejected or inconclusive")
        if not isinstance(hypothesis_id, str) or not re.fullmatch(r"h[1-5]", hypothesis_id):
            raise ValueError("hypothesis_id must be an h1-h5 identifier")
        if not isinstance(evidence_refs, tuple) or not 1 <= len(evidence_refs) <= 8:
            raise TraceBlocked("resolution requires 1-8 evidence references")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 500:
            raise ValueError("rationale must be text of at most 500 characters")
        events = self.queue.events(ticket_id)
        hypothesis_event = next(
            (event for event in reversed(events) if event["event_type"] == "failure.trace_hypotheses"),
            None,
        )
        if hypothesis_event is None:
            raise TraceBlocked("Ticket has no recorded competing hypotheses")
        hypotheses = hypothesis_event["payload"]["hypotheses"]
        if hypothesis_id not in {item["hypothesis_id"] for item in hypotheses}:
            raise ValueError("hypothesis_id is not part of the latest Trace report")
        known_events = {event["event_id"] for event in events}
        if any(not isinstance(ref, str) or ref not in known_events for ref in evidence_refs):
            raise TraceBlocked("resolution evidence must reference existing Ticket events")
        return self.queue.record_event(
            ticket_id, "failure.trace_resolution", actor="trace",
            payload={
                "hypothesis_id": hypothesis_id, "disposition": disposition,
                "evidence_refs": list(evidence_refs), "rationale": rationale.strip(),
            },
        )

    def report(self, ticket_id: str) -> dict[str, Any]:
        events = self.queue.events(ticket_id)
        hypothesis_index = next(
            (index for index in range(len(events) - 1, -1, -1)
             if events[index]["event_type"] == "failure.trace_hypotheses"),
            None,
        )
        if hypothesis_index is None:
            raise TraceBlocked("Ticket has no recorded Trace report")
        hypothesis_event = events[hypothesis_index]
        resolutions = {}
        for event in events[hypothesis_index + 1:]:
            if event["event_type"] == "failure.trace_resolution":
                resolutions[event["payload"]["hypothesis_id"]] = event["payload"]
        hypotheses = []
        for item in hypothesis_event["payload"]["hypotheses"]:
            hypotheses.append({
                **item,
                "resolution": resolutions.get(item["hypothesis_id"], {}).get("disposition", "unresolved"),
                "resolution_evidence_refs": resolutions.get(item["hypothesis_id"], {}).get("evidence_refs", []),
            })
        return {
            "ticket_id": ticket_id,
            "failure_code": hypothesis_event["payload"]["failure_code"],
            "hypotheses": hypotheses,
        }
