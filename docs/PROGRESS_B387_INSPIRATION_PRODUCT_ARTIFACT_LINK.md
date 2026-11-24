# B387 — Link Offline Product Artifacts Back to Research Knowledge

## Gap found

The offline inspiration productizer created a verified Initiative and generated `index.html` / `product.zip`, and the closed-loop evolution wrote the Initiative ID back to the originating Research Wiki node. However, that node did not retain the exact artifact paths or integrity fingerprints. The product existed, but the knowledge graph could not directly resolve which verified package belonged to the inspiration without an additional Initiative lookup.

## Change

`evolve_inspiration_closed_loop()` now writes an allowlisted `offline_product_artifact` projection into the originating knowledge node metadata whenever offline productization succeeds:

- Initiative ID and lifecycle status
- Product relative path, HTML path, and ZIP package path
- Builder input SHA-256 and package SHA-256
- Artifact status and verification result
- Human-review-required and published flags

The projection is `null` when offline productization is not requested. It stores only the verified product record returned by `LocalProductBuilder`; it does not mark the product shipped, approve it, dispatch work, or start GPU rendering.

## Verification

- `py_compile` for `src/muse/inspiration_evolution.py` and `tests/test_inspiration_evolution.py`: PASS.
- `tests/test_inspiration_evolution.py`: **16/16 PASS**.
- `tests/test_inspiration_productization.py`: **9/9 PASS**.
- `tests/test_product_builder.py`: **6/6 PASS**.
- Combined focused evidence: **31/31 PASS**; all tests use temporary workspaces. No full-suite run was started.

## Remaining boundaries

- This links generated artifacts to the Research Wiki node; it does not yet import user-edited browser localStorage/export JSON back into Muse's knowledge graph.
- Products remain `testing`, unshipped, and require human review.
- No video rendering, GPU dispatch, publication, or autonomous runtime was started.
- Latest isolated full-suite baseline remains B385: 2,475 tests / 0 failures / 0 errors. B386 and B387 have focused-only evidence.
