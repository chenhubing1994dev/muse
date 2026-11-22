# Muse Universal Skill Fusion

## Product intent

Universal Skill Fusion is the single intake path for turning an external source
project into reusable Muse capabilities and cross-capability relationships. It is
not a package installer and must not create a second scheduler. Muse Commander
remains the only execution and dispatch authority.

## Inputs

- A public GitHub repository page URL.
- A direct GitHub or GitLab ZIP archive URL.
- A local ZIP source archive uploaded from the dashboard.

The first implementation intentionally supports GitHub repository pages and
direct GitHub/GitLab archive URLs. Other hosts and protocols are rejected.

## Processing lifecycle

1. Receive exactly one source URL or ZIP archive.
2. Download only from the public HTTPS source-host allowlist, or accept the
   uploaded ZIP bytes.
3. Validate archive paths, duplicate names, symlinks/special files, file count,
   expansion size and compression ratio. Never extract the archive to an
   executable project directory.
4. Retain the original validated ZIP in `data/fusion_sources/<fusion-id>.zip`
   and record its SHA-256 digest.
5. Inspect manifests, documentation, file extensions and bounded source text.
   Capability extraction is deterministic and evidence-linked to file paths.
6. Compare extracted capability identifiers and domains with Muse's existing
   Capability Registry. Produce explicit overlap/domain relationship edges.
7. In the same submission, automatically register candidate metadata as
   `unverified` and write the cross-capability edges into each fusion-owned
   registry record. The records have no executable path and carry a
   `never_execute_untrusted_source` policy.
8. Optional human review is recorded separately. Independent tests,
   source/license review and an explicit activation gate
   are required before any candidate can become schedulable.
9. A rollback marks only the fusion-owned capability records `disabled`;
   source archives and audit records remain available.

## Safety limits

- HTTPS only; GitHub/GitLab source host allowlist.
- Redirect destinations are checked against the same allowlist.
- DNS answers must be public IP addresses; private, loopback and link-local
  destinations are rejected.
- Uploaded/downloaded archive: maximum 8 MiB.
- Archive entries: maximum 300.
- Total uncompressed size: maximum 32 MiB.
- Individual inspected text file: maximum 512 KiB.
- Total inspected text: bounded to 2 MiB.
- Retained source archives: at most 50 archives and 400 MiB combined.
- Reject absolute paths, drive paths, parent traversal, duplicate paths,
  symlinks, special files and suspicious compression ratios.
- Never import, execute, install, or launch source-project code during intake.
- No external AI API is required for deterministic first-pass analysis.

## Local API

- `POST /api/v1/fusion/submit`: submit one source URL or a base64 ZIP archive.
- `GET /api/v1/fusion/jobs?limit=20`: list recent fusion reports.
- `GET /api/v1/fusion/jobs/<32-hex-id>`: retrieve one fusion report.
- `POST /api/v1/fusion/jobs/<32-hex-id>/approve`: optionally record explicit
  human review of an already-integrated unverified candidate; this does not
  activate source code.
- `POST /api/v1/fusion/jobs/<32-hex-id>/rollback`: disable fusion-owned
  capability entries after explicit confirmation.

All endpoints use the existing authenticated local status API.

## Current capability extraction

The first deterministic rule set detects candidate domains including web
research, video editing, speech synthesis, image generation, document
processing, code analysis, workflow automation, knowledge graphs and data
analysis. These are heuristics, not proof that a project implements a working
feature. Reports retain confidence, matching file paths and current Muse
capability relationships so reviewers can inspect why a match was proposed.

License detection is currently conservative: it reports whether a license file
is present, but does not yet identify the license family or establish
compatibility. A missing or unclassified license must never be treated as
permission to redistribute or execute the source.

## Deliberate follow-on work

- Add SPDX/license-family parsing and dependency/SBOM vulnerability review.
- Add language-aware AST and API-surface extraction beyond keyword heuristics.
- Build reproducible isolated test adapters for candidate capabilities.
- Generate adapters and workflow bindings that compose with existing Muse
  skills and Commander DAGs; require independent verification before dispatch.
- Add signed source provenance, version diffs, and staged activation/rollback
  across any future adapter files.
- Expand supported Git hosts only after equivalent SSRF and redirect controls
  are implemented.
