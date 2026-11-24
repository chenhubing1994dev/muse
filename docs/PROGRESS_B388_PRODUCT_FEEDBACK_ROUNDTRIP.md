# B388 — Offline Product Feedback Round-Trip

## Gap found

Muse generated a usable offline inspiration worksheet and the browser could save/export creator edits as JSON, but there was no safe ingestion path to return those observations to the Research Wiki. This left the product-creation loop one-way.

## Change

Added `src/muse/product_feedback_import.py` and `scripts/import_product_feedback.py`.

The importer:

- Accepts only the exact JSON schema emitted by the offline product, with a 1 MiB input limit, bounded text, and at most 40 hypothesis/experiment rows.
- Rejects duplicate JSON object keys at every nesting level, avoiding parser-dependent ambiguity in imported creator feedback.
- Requires the referenced Initiative to exist, match the worksheet title, and remain in `testing`.
- Re-verifies the current local product manifest, HTML, ZIP contents, and hashes before importing; modified artifacts are rejected.
- Stores creator observations as a typed Research Wiki `experiment` node, linked to the exact Initiative.
- For inspiration-derived Initiatives, also links the exact source claim to the feedback experiment with an idempotent `tested_by` edge; no fuzzy node lookup is used.
- Uses a deterministic content digest: identical imports reuse one node; changed observations create a new append-only experiment record.
- Explicitly marks the content as unverified creator observation, not external factual evidence; independent review is required and claim promotion is forbidden.
- Does not change Initiative lifecycle, approve work, dispatch tasks, or start GPU jobs.

Example:

```powershell
py -3.11 scripts/import_product_feedback.py --workspace . --file .\muse-inspiration-lab-<initiative-id>.json
```

## Verification

- `py_compile` for importer module, CLI script, and tests: PASS.
- `tests/test_product_feedback_import.py`: **5/5 PASS**.
- After B389 strict JSON parsing hardening, `tests/test_product_feedback_import.py`: **6/6 PASS**; duplicate keys are rejected at root and nested levels.
- Cross-pipeline regression after B389: inspiration evolution 16/16, offline productization 10/10, product builder 6/6, feedback import 6/6 — **38/38 PASS**; compileall PASS.
- After B390 source-claim graph linkage, the same four suites were rerun: **38/38 PASS**; compileall PASS.
- Coverage includes exact schema/initiative binding, product integrity rejection, bounded/empty payload rejection, idempotent duplicate import, and append-only changed observations.
- Tests use temporary workspaces; no production database was opened and no full-suite run was started.

## Remaining boundaries

- Import is an explicit local CLI action; the offline HTML remains disconnected from the server and does not auto-upload browser data.
- Imported creator observations remain unverified until an independent review/research step.
- No product was shipped or published, no GPU render was run, and the autonomous runtime remains stopped.
