# Muse Software Recreation Engine

## Mission

Enable Muse to study software or functions the user is authorized to examine, create a traceable feature specification, then deliver an independently built and tested implementation through comparison and bounded refinement.

“Recreate or create any function” is a capability vision, not a promise of 100% success for every platform, application, or complexity. Coverage depends on target type, observability, provider availability, authorization, test environment, and resources.

## Unified workflow

1. Target Intake: record target type, locator, desired function, and authorization assertion.
2. Authorization Gate: Clean-room by default; Deep requires an explicit authorization scope.
3. Study: REA supplies available static/runtime evidence; Craft Anything supplies Study and comparable observations.
4. Evidence Normalize: preserve source, hash, observation, inference, counter-evidence, unknowns, and limitations in Muse knowledge stores.
5. Feature Spec: define inputs, state, outputs, edge cases, failure paths, non-functional requirements, and acceptance tests.
6. Build: Muse Commander assigns existing coding agents; code changes use the existing approval and execution path.
7. Check: Craft Compare plus Muse TestRunner compare visual, behavioral, structural, and performance dimensions separately.
8. Refine: at most 1–5 bounded iterations; every iteration preserves gaps and failure experience.
9. Review: an independent reviewer checks implementation, tests, license, target authorization, and asset provenance.
10. Package: ProductBuilder creates a versioned product, manifest, test report, and downloadable delivery bundle.

## Scheduling boundary

- Muse Commander is the only scheduler.
- REA and Craft Anything are capability providers, not autonomous schedulers.
- External MCP servers may not create Muse Tickets, start Workers, or modify Muse project files directly.
- MCP sessions use literal command argv, minimal environment, exact tool allowlists, timeouts, and bounded response sizes.
- Tool catalogs and annotations are discovery data only; Muse-side permissions, approvals, and target scope remain authoritative.

## Authorization and safety

- Only examine targets the user owns or is explicitly authorized to inspect.
- Do not bypass DRM, logins, anti-cheat, licenses, or access controls.
- Do not copy protected code, copy, logos, images, audio, video, or brand assets without rights.
- Deep mode records a specific authorization scope; sensitive data and third-party credentials are denied by default.
- Clean-room constrains the analysis method; it is not an operating-system sandbox.
- Unknown build tools, dependency install scripts, external downloads, and arbitrary commands require separate review/approval.
- Evidence records source, time, target hash, provider version, coverage, confidence, and limitations.
- Unproven behavior remains unknown; missing observations are not treated as absence or a pass.

## Ready criteria

A project may enter user acceptance only when:
- Target authorization and scope checks pass.
- Core acceptance tests have real execution evidence.
- Critical gaps are resolved and remaining gaps/unknowns are visible.
- Independent review passes.
- Delivery hashes, integrity, and download paths are verified.
- The product remains in testing until the user accepts it; only then may it become shipped.

## First-phase implementation boundary

- `muse.software_recreation.build_recreation_plan` creates a deterministic, authorization-aware Study/Build/Check plan only. It is consumed by the existing `SoftwareCraftingAdapter.enqueue_task` and Muse TaskQueue; no second scheduler or duplicate Ticket DAG exists.
- `muse.software_crafting.MCPStdioClient` remains the single bounded MCP transport for REA/Craft: fixed argv, `shell=False`, minimal child environment, exact tool allowlists, annotation checks, bounded JSON-RPC frames, timeouts, and per-call approval. The temporary duplicate `MCPStdioSession` implementation was removed.
- REA 6.5.0: MCP initialize/tools/list and a read-only `list_cutter_sessions` call were verified. `analyze_javascript_application` is executed through REA's official CLI path because its MCP call returned no JSON-RPC tool result. A real analysis of Muse's own `ui-prototype` completed; evidence and a SHA-256 manifest are stored under `data/software_crafts/b100-ui-prototype/rea-js-summary-5/`.
- Craft Anything v0.6.1: the Windows x64 portable archive SHA-256 matches the official release digest; ZIP integrity and installed CLI Authenticode signature were verified. The actual MCP `core_session_show` call succeeded. Website Study currently stops at Craft's own `consent_required` gate; Muse does not bypass it. Build/Check still require a real end-to-end acceptance run.
- Craft's local builder was configured and connectivity-tested through the existing adapter. `configure_local_backend` now accepts Muse's actual `[models]` TOML table as well as legacy `[model]`; `Muse Local` / `llama_cpp` / `DeepSeek-V4-Pro-Qwen3.5-9B` returned `ready` from the loopback connectivity test.
- ego-lite v2.2.0 source is pinned at commit `bacaece43900e8ca68eea3b6f1663835ea38df9e`. `muse.ego_browser` implements runtime probing, public Google search, dynamic page capture, screenshot/source metadata, bounded same-origin image/PDF collection, and provenance manifests. The Windows desktop app is not installed yet, so live browser execution is not accepted as complete.
- `muse.browser_research` augments the existing ResearchPipeline: curated support/counter queries can add Google results; the existing safe HTTP reader remains primary; ego-lite is a public-only fallback for dynamic/short/challenge pages. Up to three same-origin image/PDF assets may be collected for internal research (2 MiB each, 5 MiB total), with rights marked unreviewed and reuse disabled.
- The plan-only `muse-recreate` CLI remains a preview tool. The production execution path stays `Muse Commander → existing TaskQueue/Worker → approval → TestRunner/ReviewGate → ProductBuilder`.
- Not yet complete: ego-lite desktop runtime acceptance, Craft website consent/Study, Craft Build/Check, and a full software-recreation product delivery. No capability is marked complete without real execution evidence.