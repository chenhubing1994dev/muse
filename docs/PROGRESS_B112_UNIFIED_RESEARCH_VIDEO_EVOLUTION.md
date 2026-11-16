# B112 — Unified Research, Video and Evolution Ledger

Date: 2026-11-12
Status: unified research/video/evolution ledger and real Wan 2.1 HTTP delivery verified. Reviewer JSON compatibility fixed; local LM Studio reviewer passes the strict review gate, and the isolated approved-change executor has passed an end-to-end promotion smoke test. Full regression suite: 1,079 tests passed. Wan 2.1 fifth and sixth shots generated, packaged and delivered through Dashboard with matching MP4/ZIP hashes. A single integrated live-model-to-capability-promotion run and desktop UI interaction remain to be verified.

Connect Research Wiki, Creator Initiatives, VideoProductBuilder and improvement experiments through the existing append-only Creator Initiative Ledger and Evolution Cycle IDs. Keep Muse Commander as the only scheduler; do not introduce a parallel ledger or scheduler.

## Implemented

1. **Research-to-initiative links**
   - Added append-only `initiative_knowledge_links` to the existing InitiativeStore database.
   - Links carry a knowledge node ID/type, relationship, Evolution Cycle ID and timestamp.
   - Added `ResearchKnowledgeStore.link_node_to_initiative()`; it writes the ledger relation first, then idempotently annotates the graph node with creator initiative and cycle references.
   - Autonomous research links ideas, research-run experiments, experiment plans, cycle/proposal-associated experiment nodes, supporting/counter-evidence sources, related claims and matching failure records to creator initiatives.
   - Improvement proposal and source feedback IDs are linked separately. Independent-review/validation decision nodes are linked by proposal or research-plan identity.

2. **Video-to-initiative artifacts**
   - VideoProductBuilder's source-assets, prompt-to-video and recovered-MP4 paths record verified outputs in the same Creator Initiative Ledger.
   - If no creator initiative is supplied, a stable video-specific creator initiative is created; callers may also provide an existing initiative and Evolution Cycle ID.
   - Product events retain MP4, ZIP, manifest, storyboard and README SHA-256 values, bounded source-asset hashes, selected generation parameters and integrity-check summary.
   - The local video API accepts optional `creator_initiative_id` and `evolution_cycle_id`, and returns linked initiative/event IDs.
   - Product status remains `testing`; local integrity verification is not human editorial approval.

3. **Unified timeline and navigation**
   - Initiative timeline includes allowlisted research-link events and video artifact metadata.
   - Product listing now resolves the separate Creator Initiative ID for video products; Dashboard timeline requests no longer confuse the video product-folder ID with its owning Creator Initiative ID.
   - Added a safe, bounded Research Wiki node detail endpoint. Dashboard timeline knowledge links can open the corresponding idea, evidence, experiment or decision node without copying IDs.
   - Older databases without the new link table remain readable.

4. **Improvement loop and capability promotion**
   - `ImprovementStore.validate_and_promote()` records approved and rejected independent-review outcomes as Research Wiki decision nodes, with baseline/candidate experiment edges and rejection findings retained. A rejected review never upgrades a capability.
   - After comparable baseline/candidate tests and approval by a distinct independent reviewer, callers may provide an existing `CapabilityRegistry` entry. Only `unverified` or `ready` entries can be promoted to `ready`, with a version derived from the candidate fingerprint and audit metadata.
   - `ApprovedCodeChangeExecutor` forwards an explicit task `capability_id`; the worker opens the CapabilityRegistry and InitiativeStore and supplies both to the executor. The resulting decision is linked back to the original Creator Initiative when its ID is present in proposal metadata.
   - Disabled, missing and approval-gated capabilities are never automatically re-enabled. User approval, hash-guarded patching, independent review and baseline/candidate test comparability remain mandatory.

## Validation evidence

- `.venv\Scripts\python.exe -m compileall -q src tests`: completed successfully after the final capability/initiative integration.
- Targeted post-integration regression: `tests.test_improvement`, `tests.test_evolution_executor`, `tests.test_worker`; **55 tests passed**, exit code 0.
- Final full suite after all current source changes: `python -m unittest discover -s tests -q`; **1,077 tests ran, OK**, exit code 0 (109.566 seconds).
- Actual local ComfyUI 0.23.0 + Wan 2.1 T2V 1.3B generation completed on AMD Radeon RX 6750 GRE 12GB (ROCm 7.14). ffprobe verified H.264, 512×288, 12 FPS, 13 frames, container duration 1.083333 seconds. This is a short preview, not a finished long-form film.
- Verified product directory: `data/products/958f814a1ec582443c28041ae72abbcb/bf14f26c32181a11c567/` containing `video.mp4`, `product.zip`, `manifest.json`, `storyboard.json` and `README.md`.
- Verified hashes: ZIP package `25b626fe3b1047fb0724ee5c2350fa1069714576e446c424d5d45b5843f9aa71`; MP4 `f1926a22acba8c94c76de04fd006bf38292823ae10ce30143056e0c0573e2f22`; manifest `c3774d591014f67fc5baec4c1f95d63b8506759da50b21b49818dd2a9c682a91`.
- Artifact is linked to Creator Initiative `67a86c835b5c48a4999e59f050195d58`, with product ledger event `197e47730867416e9b94e561a7516b09`; initiative remains `testing`, not shipped.
- Live HTTP verification against the real product passed: product listing resolved Creator Initiative `67a86c835b5c48a4999e59f050195d58`; timeline returned 3 events; ZIP download was 103,124 bytes and matched the ledger SHA-256; MP4 download was 103,266 bytes and matched its ledger SHA-256; preview returned `video/mp4`. ZIP members were `README.md`, `storyboard.json`, `video.mp4`.
- New integration test `tests.test_video_dashboard_integration` verifies product/creator ID mapping, timeline knowledge links, safe knowledge-node detail retrieval, ZIP integrity and MP4 hash.
- Dashboard `svelte-check`: **0 errors, 0 warnings**; frontend TAP suite: **17 tests passed**; Vite production build completed successfully. This validates compile/build/API behavior, not a live user-operated desktop session.
- The earlier ComfyUI job `wc_job_yQpcX8CQbOF2ViL0` timed out at 1,800 seconds. It was restarted as long-lived Runner Job `wc_job_PvlOfyZH97Wuvnd_` with a 7-day execution budget; `/system_stats` returned ready, ComfyUI 0.23.0, ROCm 7.14 PyTorch and one available GPU device.
- Project formatting was not admitted because the repository's `pyproject.toml` has no Ruff `target-version`; no project configuration was changed just to satisfy the formatter.

## B113 follow-up — independent reviewer output compatibility

- Root cause of the prior live smoke rollback: the local model returned a valid approval object inside one Markdown `json` code fence; `IndependentReviewGate` previously accepted only bare JSON, so parsing failed closed and the executor correctly rolled the candidate patch back.
- `src/muse/review_gate.py` now accepts either plain JSON or exactly one complete fenced JSON object. It still rejects preambles, trailing prose, malformed JSON, non-object payloads and oversized output; the existing field/type checks and critical/high finding veto remain unchanged.
- Added regression coverage for fenced JSON acceptance, preamble rejection and preservation of the high-severity veto.
- Live LM Studio smoke: the configured Gemma model returned a fenced JSON approval; the updated `IndependentReviewGate` parsed it, persisted the review record and retained the low-severity finding. Reviewer identity was `local-reviewer:fec5ca45f08bc6f1da3e3f18`.
- Isolated executor integration smoke with the same observed model response shape: baseline failed 1/1, candidate passed 1/1, independent-review path approved, proposal transitioned to `implemented`, capability changed from `unverified` to `ready`, and the validation decision linked to the Creator Initiative. The smoke ran in a temporary workspace and did not modify the production source tree.
- Targeted suites (`test_review_gate`, `test_experiment_bridge`, `test_improvement`, `test_evolution_executor`): **46 tests passed**.
- Full regression suite using the project virtual environment (`.venv\\Scripts\\python.exe`): **1,079 tests ran, OK** (109.722 seconds). A first run under the global Python 3.14 interpreter failed because that interpreter lacked the `tzdata` package; the project `.venv` has the required timezone database and is the validated environment.
- Remaining video/service verification: generate a more dynamic shot and assess multi-shot continuity; verify interactive Dashboard browser behavior; move ComfyUI into a managed lifecycle with crash recovery rather than relying on the current bounded Runner foreground Job; continue audio/Chinese voiceover integration.
## B114 follow-up — Wan 2.1 fifth shot and live Dashboard delivery

- ComfyUI 0.23.0 is ready on loopback port 8188; detected AMD Radeon RX 6750 GRE 12GB and the installed Wan 2.1 T2V 1.3B + VAE assets. Current foreground Runner Job `wc_job_LBmCN1ZoveLAfT7Z` holds the process with a 3,600-second Runner limit; this is not yet a permanent Windows background service.
- Generated `data/video_sources/muse-cosmos-film/shot-05.mp4`: seed `20261112`, prompt ID `5863beb5-122d-45c1-b32e-1dcd8dab2999`, 9:16, 288×512, 12 FPS, 61 frames, 8 steps, H.264, 5.083333-second container duration, 35,221 bytes; SHA-256 `2f7fc7227e84b896be7aaa1e7060b51d76175a81e36ee43cf32732d5850aa05d`.
- A first package call supplied an invalid `scene_records` shape and failed before packaging; the MP4 was retained and successfully packaged by letting the builder create its default scene record. No second GPU generation was performed.
- Verified video product: `a78513d0eade36edcb274c6ddedb43e7`, version `6eceee66538f593c6271`, Creator Initiative `dfe664cf79cf4870958a7366f7bd1fa1`, ledger event `ae3a99c2d77a42b8891f5230015bf9e1`; status remains `testing`.
- FFprobe confirms H.264 / 288×512 / 12 FPS / 61 frames; FFmpeg full decode exits 0. Builder integrity checks cover MP4 SHA-256, ZIP CRC/members/content hashes.
- Restarted the `MuseDesktopDashboard` scheduled task to load current source. Dashboard health HTTP 200; product listing resolves the Creator Initiative ID. `/video` and `/preview` each returned HTTP 200 and 35,221 bytes with MP4 SHA-256 matching the local file. `/download` returned HTTP 200 and 32,110 bytes with ZIP SHA-256 `77823911356ca45a0e8d01819e9d254747b1ceaaf5971cf5555c4ceb369cd429`. Initiative timeline returned 3 events including the product event.
- Three-frame visual contact sheet shows stable composition and subject without obvious deformation, but motion is limited; treat this as a preview-level establishing shot, not a finished dynamic film. No audio track is present.

## Remaining work before declaring full fusion complete

1. Run one live, model-driven feedback → proposal → research → code patch → baseline/candidate test → distinct independent reviewer → capability-registry promotion cycle. Current tests prove the integration path, but do not substitute for a live model/reviewer run; a rejected review remains rejected and must not upgrade a capability.
2. Launch the updated Dashboard in the actual desktop/browser environment and interactively verify product timeline expansion, knowledge-node detail, MP4 preview and both download actions.
3. ComfyUI repeated-load stability remains unresolved: a second prompt in the same process crashes in CLIPLoader/load_torch_file with Windows 0xC0000005. Use one generation per fresh ComfyUI process as a temporary workaround; implement managed restart/crash recovery before claiming production stability.
4. Rerun the full suite after any further source changes; the current full regression result is 1,079 tests passed.

## Safety and evidence rules retained

- A search miss or a failed experiment is not evidence that a claim is false.
- Symbolic/metaphysical inspiration remains hypothesis generation, never factual proof.
- Only verified product artifacts are recorded as built; they are not marked shipped automatically.
- Capability upgrades remain gated by explicit approval, comparable tests, independent review and a traceable validation decision.

## B115 follow-up — Wan 2.1 sixth shot and Dashboard delivery

- The more dynamic second-shot generation was allowed to finish after queue inspection and Windows process CPU/memory growth confirmed inference was active; no duplicate prompt was submitted.
- ComfyUI prompt 6bd3aff6-ad05-46cb-bc2b-ef92f19a81f0 completed with Wan 2.1 T2V 1.3B, seed 20261113, 9:16, 288×512, 12 FPS, 61 frames, 16 steps. MP4: 177,080 bytes, 5.083333-second container duration, H.264; SHA-256 92fcfa3b3fa1ac9fceb49e65b4e563deecb67cd8260a61808813e84eb0f6d24e.
- VideoProductBuilder packaged Muse Cosmos Film — Shot 06, validated=true; product ID 8fac9a3ca555cad5b69689a282b9581d, version 84fb3f41af1e55fc5fd5, Creator Initiative 66064b712195460b885f036d204cd902, ledger event d869cbeab4fc4cf78c2b6f5e2f10422d. Product remains testing.
- FFprobe confirms H.264 / 288×512 / 12 FPS / 61 frames; full FFmpeg decode passed. Dashboard product listing finds the product. /video and /preview both returned HTTP 200, 177,080 bytes and the MP4 hash matches local output. /download returned HTTP 200, 175,955 bytes, ZIP SHA-256 1dfb60a689e0165980fd9aeeacc5845c0e9dfb6dc0c4d194aa1fe8392a84fa79. Initiative timeline returned 3 events including the product event.
- A three-frame contact sheet shows continuity of the portal, bridge and explorer; the explorer's pose/position changes, with modest environmental particle changes. Motion remains limited and is not yet a finished action shot or feature-film quality. No audio track.
- Generation completed in the current Runner session. Do not describe the foreground ComfyUI process as a permanent Windows service. Remaining work: stronger visible motion, multi-shot character/scene continuity, audio/Chinese voiceover, live desktop interaction.
- Repeated-load investigation: after shot-05, a second prompt in the same ComfyUI process crashed during CLIPLoader/load_torch_file with Windows 0xC0000005. --disable-async-offload / --disable-mmap and --disable-pinned-memory / --lowvram did not fix it; the lowvram first prompt also crashed during CLIP loading. Restoring default settings and launching a fresh process allowed shot-06 to complete. Current process reports 8188 health ready and queue empty after one job. Temporary safe rule: one generation per process, restart before the next prompt; root cause across ROCm 7.14 / PyTorch 2.10 / safetensors remains open.

- After B115 artifact and documentation updates, the complete project virtual-environment unittest suite passed again: **1,079 tests**, 108.494 seconds, exit code 0. No source-code changes were needed for this shot.
## B116 follow-up — six-shot teaser rough cut v1

- All six source shots share H.264, 288×512, 12 FPS. Shots 01–04 are 2.083333 seconds each; shots 05–06 are 5.083333 seconds each. Total: 18.5 seconds / 222 frames.
- FFmpeg concat demuxer assembled shots 01–06 in order with hard cuts and no re-encoding into data/video_sources/muse-cosmos-film/teaser-rough-cut-v1.mp4: 343,192 bytes, SHA-256 9423aca20297fd028721b727867ab74edd110c1a6dd4e660d478a375633c6a47.
- The first packaging attempt was correctly rejected because metadata did not declare status=completed; the retry supplied completed editorial-assembly metadata and six allowlisted scene records. VideoProductBuilder validation passed; source shots were not modified and no GPU rerender was performed.
- Product Muse Cosmos Film — Teaser Rough Cut v1: product ID 59148fb9f5299ad0292157ed38194996, version f0140e0b32facf564f95, Creator Initiative b21ad2a245694cae80a04b7f6841e2cf, ledger event 070ded7bcb324f108ed2224a55aab939; status remains testing.
- Full FFmpeg decode passed. Dashboard /video and /preview returned HTTP 200, 343,192 bytes and the MP4 SHA-256 matches local output. /download returned HTTP 200, 332,340 bytes, ZIP SHA-256 b8de636cd761911ebe00243695537c3a5a6e216a90741194e6f848efdb8f6d3e. Initiative timeline returned 3 events including the product event.
- The six-shot contact sheet shows a coherent teal/gold fantasy visual direction, but subject and scene continuity varies between shots. This is an 18.5-second silent hard-cut rough cut only; it has no title package, transitions, grading, music or Chinese narration and must not be described as a finished promotional film.