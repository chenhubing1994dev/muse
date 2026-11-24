# B388 — Bounded iterable input for inspiration productization

Date: 2026-11-24

## Defect

`_bounded_rows()` enforced a maximum row count only after materializing the entire input iterable with `list(values)`. A very large or unbounded generator could therefore consume unbounded time and memory before the limit was checked.

## Change

- Changed `_bounded_rows()` to obtain an iterator and validate each row incrementally.
- The function now rejects immediately when it encounters item 41 (default maximum 40), without consuming the rest of the iterable.
- Added `test_bounded_rows_stops_consuming_an_unbounded_generator`, using an endless generator and asserting exactly 41 items were consumed before rejection.
- No production database, generated product package, external network, dispatcher, or GPU path was used by the regression tests.

## Validation

- `tests/test_inspiration_productization.py`: **10/10 passed**.
- `tests/test_inspiration_evolution.py`: **16/16 passed**.
- Combined focused regression: **26 tests passed, 0 failures, 0 errors**.
- The productization module was also run independently after the change: **10/10 passed**.
- An initial test invocation from `.muse-test-sandbox` failed to import the `tests` package because that isolated cwd does not contain the test tree. No tests ran in that failed invocation; reran correctly from project root with PYTHONPATH=src and unittest discovery. This is a test-command/cwd issue, not a product regression.

## Safety and remaining work

- Offline productization remains draft/testing and requires human review. It does not publish, dispatch, start GPU work, or apply generated source changes.
- Plugin execution remains fail-closed pending a real isolated WASM runtime, capability broker, quotas, Commander one-shot grant consumption, audit and kill/rollback integration.
- Full suite was not rerun: the latest isolated baseline remains 2,475 passing tests before this focused change. Avoid duplicate full-suite runs; run only affected gates until the next justified integrated snapshot.
