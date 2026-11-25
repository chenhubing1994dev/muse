# Muse Video Motion

Remotion is Muse's deterministic motion-graphics adapter. VideoProductBuilder invokes it when the selected profile is brand_intro_outro.

## Production responsibilities

- ComfyUI / Wan: generate source footage.
- Muse VideoProductBuilder: manage source assets, generation metadata, fingerprints, staging, QA, manifests and product ZIPs.
- Remotion: render branded intro/outro bumpers and animated title compositions.
- FFmpeg: crossfade bumpers into the source MP4, preserve and mix audio, encode the final master.
- Muse Video QA: FFprobe, full decode, black/freeze/silence signals and SHA-256 verification.

## Local setup

Use Node.js 24 (the Muse Runner currently uses Node 24.20.0).

PowerShell commands:
- cd "<MUSE_ROOT>\video-motion"
- npm install
- npm run check
- npm run studio

The MuseBumper composition is parameterized by title, subtitle, accent, width, height, FPS and duration. MuseBrandMotion remains available as a standalone brand sequence.

## Render and integration verification

From the Muse project root, run:
- npm run render:portrait (inside video-motion)
- npm run render:landscape (inside video-motion)
- .venv\Scripts\python.exe scripts/verify_remotion_motion.py

The integration smoke test creates temporary source footage, exercises both music and narration timing, runs the real Remotion renderer and FFmpeg crossfades, then tests all three VideoProductBuilder entry points. The Wan generation call is mocked in this test so no GPU generation is claimed or consumed.

## Product profile

- none: preserve the legacy no-motion output.
- brand_intro_outro: render a 2-second animated opener and 1.8-second closer, then crossfade them with the main video.
- Music mode starts the soundtrack at the opener and fades it out at the end.
- Narration mode starts the original audio at the beginning of the main footage; intentional silence in the branded bookends remains a QA review signal.
- Motion metadata is stored in storyboard.json and manifest.json; product version fingerprints include the selected motion profile.
- Any Remotion failure is a hard product-build failure. Muse never silently publishes a video without the requested motion profile.

This is now an integrated post-production stage, not a standalone template demo. A finished product is only reported after FFprobe, full decode, hash checks and product ZIP verification succeed.
