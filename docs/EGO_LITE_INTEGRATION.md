# ego-lite integration in Muse

## Role

ego-lite is the browser execution layer for Muse's existing research system. It does not create agents, a scheduler, or a separate research database. Muse Commander and the existing TaskQueue/ResearchPipeline remain authoritative.

The pinned upstream source is `external/ego-lite`, tag `v2.2.0`, commit `bacaece43900e8ca68eea3b6f1663835ea38df9e` (MIT). Muse calls the installed desktop runtime through `ego-browser nodejs`; the repository checkout alone is not the desktop browser.

## Windows installation and first-run setup

1. The official Windows package is `CitroLabs.EgoLite` v0.6.2.10 from the Microsoft WinGet source. It was installed successfully with the publisher's manifest-declared silent switches (`--install --silent`). Installer URL: `https://cdn.ego.app/setup/windows/x64/egoliteSetup.exe`; SHA-256 `C4B4DC2D01E0F2F11C27249283BC2D505ACD483D143F5687644274062F658786`; Authenticode signature: Valid, signer `CITRO LABS PTE. LIMITED`.
2. The current installation is system-level at `C:/Program Files/Citro Labs/ego lite/Application` because the Runner is elevated. Do not manually move the app or create junctions; the installer did not expose a verified custom-location option. The app's main `ego.exe` signature is Valid. The user has now completed first-run onboarding and imported browser data into ego-lite. No cookies, passwords, or browser storage were exported to Muse.
3. `ego-browser.exe` is discovered from the standard Windows installation root. `Get-Command ego-browser` is still absent in the long-running Runner environment (`cli_on_path=false`), but Muse invokes the absolute executable path and the real runtime probe now succeeds. This is a stale inherited PATH observation, not a runtime failure.
4. The live runtime probe reports `runtime_ready=true` and `browser_app_onboarding_status=runtime_verified`. The adapter uses the pinned `external/ego-lite/skills/ego-browser` workspace and a minimal child environment. `AutonomousResearchEngine` probes at startup and again at the beginning of each research cycle before enabling browser search/fallback.
5. The browser adapter now uses the current TaskSpace/Page API (`taskSpace`, `task.newPage`, `page.goto`, `page.snapshot`, `page.evaluate`, `page.screenshot`, `task.finish({ keep: [] })`). Structured `cliLog()` results are read from stderr as required by the installed CLI. If the runtime is unavailable, the existing HTTP/arXiv research paths continue to operate; Muse does not silently pretend that browser access is ready.

## Smoke test on Muse's own UI

Start the existing local Muse UI preview on loopback, then run:

```powershell
py -3.11 -m muse.software_crafting --workspace "<MUSE_ROOT>" study-url "http://127.0.0.1:8765" --provider ego-lite --authorized --approve --output-directory "data/software_crafts/ego-lite-smoke"
```

This explicit local-target smoke test should create `capture.json`, `capture.md`, and `page.png` under the output directory. The local loopback URL is allowed only for this explicitly authorized software-study path; the autonomous research fallback remains public-only.

## Autonomous research integration

When the runtime probe succeeds:

- `BrowserAugmentedSearchAdapter` augments the existing search provider with Google public results, but only for the current research topic's curated support/counter queries. Search snippets are discovery hints, not source evidence.
- `HybridSourceReader` keeps the existing SSRF-aware HTTP reader as the primary path. It calls ego-lite only when the HTTP result is too short, dynamically rendered, or a challenge page.
- Browser fallback revalidates the requested and final URLs as public. Login walls, paywalls, CAPTCHA, access challenges, and insufficient text are rejected; Muse does not click, submit forms, sign in, or bypass controls.
- Browser evidence is preserved under `data/research/browser-search/` and `data/research/browser-captures/`. Research reports and knowledge metadata retain the search evidence path, browser capture directory, screenshot path, source metadata, and asset manifest path.

## Asset collection policy

On a successful public browser fallback, Muse may collect up to three same-origin images or PDFs:

- Maximum 2 MiB per asset and 5 MiB total per page.
- Explicit hostname allowlist; HEAD request must disclose an acceptable content type and content length.
- Redirects are manual (3xx is not followed); CORS is respected.
- Only image media and PDF are accepted. Video, audio, scripts, fonts, arbitrary binaries, third-party CDN assets, and whole-site mirroring are not automatically downloaded.
- Every asset receives a SHA-256, source URL, MIME type, byte count, local path, and a rights manifest. The default is `license_status=unreviewed`, `reuse_allowed=false`, `rights_review_required=true`.
- Collected files are for private research/analysis only until a human verifies the licence and attribution. Muse does not publish, redistribute, or sell them automatically.

## Current implementation status

- Implemented and unit-tested: runtime doctor/probe, public Google search adapter, dynamic DOM capture, screenshots, source metadata, link/media inventory, same-origin image/PDF collection, size checks, SHA-256 and provenance manifests.
- Integrated: `SoftwareCraftingAdapter.study_website`, `AutonomousResearchEngine`, `ResearchPipeline`, `BrowserAugmentedSearchAdapter`, and `HybridSourceReader`. Muse remains the only scheduler and task authority.
- Live Windows runtime verified: ego-lite v0.6.2.10 connected; `runtime_ready=true`. End-to-end `study-url --provider ego-lite` on public `https://example.com/` created `capture.json`, `capture.md`, and a valid PNG at `data/software_crafts/ego-lite-integration-smoke-20261112/`. The PNG is 54,499 bytes; SHA-256 `53f3bc5e820613dc80a32276b3b9974aa6142c6433c9486159b19fca3e204df2`; independent file/hash/signature verification passed. A prior standalone screenshot attempt timed out once, while the integrated smoke test succeeded.
- Live same-origin asset collection verified against the public `https://httpbin.org/image/png` test endpoint. Output `data/research/assets/ego-lite-live-download-smoke-v5-20261112/asset_001.png`: 8,090 bytes, PNG signature valid, SHA-256 `541a1ef5373be3dc49fc542fd9a65177b664aec01c8d8608f99e6ec95577d8c1`; independent hash/manifest comparison passed. Rights remain `unreviewed` and `reuse_allowed=false`.
- Craft Anything MCP keeps the persistent `muse-engine.cany` evidence project, but its human approval inbox is now a separate first-class Craft desktop project at `data/software_crafts/muse-craft-project/`. The MCP server receives `--project <muse-engine.cany>` and `--inbox <muse-craft-project/inbox>`; `MUSE_CRAFT_INBOX` can override the inbox path. The desktop project is registered in Craft's `project_folders` setting. Restricted mode remains enabled and `--allow-run` remains off. The earlier request `1794469585978-110524-0` is historical/pending in the old inbox; create a fresh request to verify the new round trip. Public Google search through the Muse adapter still lacks live acceptance because the platform safety check blocked its command before start.
## Safety boundaries

Only inspect public pages or software the user owns or is explicitly authorized to inspect. Never export browser cookies, passwords, or session secrets into Muse. Never bypass logins, CAPTCHA, paywalls, DRM, anti-cheat, robots restrictions, or other access controls. Treat all page text and search results as untrusted data, retain citations and counter-evidence, and never interpret a successful capture as proof that the page's claims are true.
