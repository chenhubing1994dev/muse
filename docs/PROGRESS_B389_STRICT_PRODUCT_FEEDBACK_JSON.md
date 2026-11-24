# B389 — Strict Product Feedback JSON Parsing

## Gap found

The offline product feedback CLI used Python's default JSON decoder, which silently accepts duplicate object keys. A worksheet with repeated keys could therefore be interpreted differently by other parsers or reviewers, creating ambiguity in creator observations.

## Change

- Added `parse_product_feedback_json()` to enforce UTF-8 JSON input and the existing 1 MiB byte limit.
- Added an `object_pairs_hook` that rejects duplicate keys at every nesting level.
- Routed the CLI through the strict parser; malformed input remains a rejected import and cannot reach Initiative or Research Wiki persistence.
- Kept the existing exact schema, bounded text/rows, artifact integrity verification, idempotent digest, append-only changed observations, and unverified epistemic status.

## Verification

- `py_compile` for importer, CLI, and tests: PASS.
- `tests/test_product_feedback_import.py`: **6/6 PASS**.
- Regression covers duplicate keys at root and nested levels, plus oversize input.
- CLI `--help`: PASS.
- Cross-pipeline regression: inspiration evolution 16/16, offline productization 10/10, product builder 6/6, feedback import 6/6 — **38/38 PASS**; compileall PASS.
- Tests use temporary workspaces; no production DB was opened and no full-suite run was started.

## Remaining boundaries

- Import remains an explicit local CLI action; the offline HTML does not auto-upload browser data.
- Creator observations remain unverified and require independent review; claim promotion remains forbidden.
- No Initiative lifecycle change, Commander dispatch, GPU work, video rendering, publication, or autonomous runtime start occurred.
- Latest isolated full-suite baseline remains B385: 2,475 tests / 0 failures / 0 errors; B386–B389 have focused-only evidence.
