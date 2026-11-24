# B390 — Close Creator Feedback Back to Its Source Inspiration

## Gap found

B388 imported creator feedback into the Research Wiki and linked the experiment to its Initiative, but an offline inspiration product's original source claim was only indirectly reachable through the Initiative concept. The graph did not express that the original inspiration had now been tested by creator feedback.

## Change

- Read the exact `inspiration_productization.inspiration_id` from the Initiative's stored concept; no fuzzy title or latest-node lookup is used.
- For productized inspiration Initiatives, require that the referenced active Research Wiki node exists and is a `claim` before importing.
- Create an idempotent `tested_by` edge from the source inspiration claim to the creator-feedback experiment node.
- Store the exact inspiration node ID on the experiment metadata and return whether the source link was created.
- Keep creator feedback unverified; the edge records a test relationship, not support, refutation, evidence, or claim promotion. Generic Initiatives without inspiration-productization metadata retain the existing Initiative-only link.

## Verification

- `tests/test_product_feedback_import.py`: **6/6 PASS**.
- Tests verify exact source-claim linkage, idempotent edge reuse, append-only new experiment/edge for changed feedback, and no claim promotion.
- Python compile checks: PASS.
- Cross-pipeline regression: inspiration evolution 16/16, offline productization 10/10, product builder 6/6, feedback import 6/6 — **38/38 PASS**; compileall PASS.
- Tests use temporary workspaces; no production database was opened and no full-suite run was started.

## Remaining boundaries

- The browser worksheet still exports a local JSON file; import remains an explicit local CLI action.
- Creator observations still require independent research/review before any claim lifecycle transition.
- No Commander dispatch, GPU render, video generation, publication, or autonomous runtime start occurred.
