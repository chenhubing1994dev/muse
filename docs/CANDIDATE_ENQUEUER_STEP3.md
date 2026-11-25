# Research Candidate Enqueuer — Step 3

## Selector integration

Candidate selection does not duplicate the creator-hypothesis eligibility or ranking rules. The production builder creates a disposable backup of the Research Wiki and calls AutonomousResearchEngine._next_topic(preferred_key="creator-hypothesis-review:<node-id>") for each claim id. The engine's actual selector decides whether the requested creator-inbox candidate is eligible and supplies support_query/counter_query.

The formal knowledge database is never passed to ResearchKnowledgeStore during planning. Its schema bootstrap runs only against the temporary copy, which is discarded after the plan is built.

## Ticket structure

Each eligible Claim produces up to three idempotent Commander TaskQueue tickets:

1. evidence_search child — reads metadata.support_search_direction and carries the selector's support_query.
2. counter_evidence_search child — reads metadata.counter_search_direction and carries the selector's counter_query.
3. research_parent — depends on both child task IDs and carries the evaluation policy.

Each task idempotency key is SHA-256 of:

    node_content_sha256 + ":" + task_type + ":" + research_round

The two child tasks and parent use recovery_policy=manual. Existing failed/cancelled tasks are not retried. The explicit --retry-failed flag advances research_round and creates new deterministic keys without changing old failed tasks.

## Knowledge metadata

On apply, only metadata_json and updated_at are changed on the existing proposed Claim. research_status becomes queued and the task IDs, task keys, round and timestamp are recorded. The Claim's title, summary, status, external_ref, review_state and content hash are not changed.

## Safety and backup

Default command is dry-run. It reads the knowledge and task databases in read-only mode, and topic selection uses a temporary SQLite backup. It does not instantiate TaskQueue and reports database_writes=0 and snapshots_created=0.

With --apply, both knowledge and TaskQueue databases are integrity-checked and backed up with sqlite3.Connection.backup() before TaskQueue is instantiated or metadata is updated. Snapshot filenames are unique; any collision aborts without overwrite. TaskQueue inserts use BEGIN IMMEDIATE and rely on its existing unique idempotency_key constraint.

The three ticket writes are individually committed by the existing TaskQueue API. If a process fails partway through, rerunning the same plan fills missing idempotency keys and does not duplicate existing tasks. The Claim metadata is updated after the task IDs are known.

## Commands

    $env:PYTHONPATH = "$PWD\src"

    # Preview only; no formal database writes or snapshots.
    py -3.11 -m muse.candidate_enqueuer --project-root . --max-candidates 5

    # Explicitly create tickets and update research_status.
    py -3.11 -m muse.candidate_enqueuer --project-root . --max-candidates 5 --apply

    # Retry failed research in a new round only by explicit request.
    py -3.11 -m muse.candidate_enqueuer --project-root . --max-candidates 5 --retry-failed --apply

## Verified current dry-run

The current formal databases were inspected read-only. The selector returned one eligible candidate:

- Claim: 5eea765fb4e79628976d3a015b722f1e
- SHA-256: 03595f7f16e6d397dd1d5605e3d469cae3a70cb2a83975ce76b2877291893aa4
- research_round: 1
- tasks to create: 3 (evidence_search, counter_evidence_search, research_parent)
- knowledge metadata rows to update: 1
- database_writes: 0
- snapshots_created: 0

Six other creator-inbox candidates were skipped because their metadata lacked support_search_direction. They were not modified. No formal apply was run.

## Tests

- tests/test_candidate_enqueuer.py — dry-run hash stability, idempotent parent/children, explicit retry gating, candidate limit and Claim status preservation.
- tests/test_task_queue.py — Commander TaskQueue regression.

The runner Python does not have pytest installed. The suites use unittest.TestCase and are pytest-discoverable when pytest is available; no package was installed.
