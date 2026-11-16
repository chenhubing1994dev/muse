# Muse Video Narration Pipeline

## Scope

Muse can synthesize Hong Kong Cantonese locally and attach the voice track to an already-rendered video without repeating GPU generation. Speech synthesis, audio muxing, product packaging, and quality acceptance are separate stages with explicit verification.

## Current implementation

- `VideoProductBuilder.package_generated_clip(..., narration_path=..., narration_script=...)` accepts a regular local audio file (WAV/MP3/M4A/AAC/FLAC/OGG) and an optional script.
- FFmpeg muxes narration as AAC while copying the source video stream. `apad + shortest` prevents a short narration file from shortening the video; final QA requires an audio stream when narration was requested.
- Narration audio and script enter the product ZIP. Manifest/storyboard record language, script, file size and SHA-256; product verification checks ZIP membership and narration integrity.
- `VideoProductBuilder.package_generated_clip_with_cantonese_narration(...)` connects local TTS to the existing packaging path. It synthesizes to a temporary workspace-local WAV, packages the clip, then removes the temporary file. Returned synthesis provenance includes engine, pinned model revision, license, language, sample rate, duration and byte count.
- TTS output promotion uses `os.replace` directly. The duration-filter backend creates temporary candidates under the OS temp directory; on Windows, candidates must be on the same volume as the final WAV. The adapter temporarily directs the locked duration-filter call to the output directory and restores the prior temp setting afterward.
- `InitiativeStore.update()` now fetches its updated row directly by primary key rather than searching the first 200 listed initiatives. This fixes product publication when a new initiative falls outside the default list window.
- `src/muse/cantonese_asr.py` adds a local-only faster-whisper adapter pinned to `alvanlii/whisper-small-cantonese` revision `b03a4e5d482f06858ce10ebbabebabec9f447a0d`; it verifies the CTranslate2 model size and SHA-256 before loading and never downloads during transcription.
- ASR can compare recognized Cantonese with the narration script using punctuation-insensitive CER, produce SRT cues from actual segment timestamps, and require human listening review regardless of the machine score.
- `VideoProductBuilder.package_generated_clip_with_cantonese_narration(..., asr_model_dir=...)` can optionally run the pinned ASR after TTS, then package the timed SRT, model/audio provenance and CER report. `verify_path` checks the subtitle bytes/hash and narration-ASR linkage inside the product ZIP.
- `src/muse/video_qa.py` now runs FFmpeg EBU R128 + astats during full decode and reports integrated LUFS, true peak, sample peak, RMS and clipping suspicion. Missing measurements are explicitly unavailable; peak-based clipping is a heuristic, not an exact clipped-sample count.
- `VideoProductBuilder.package_generated_clip(..., background_music_path=...)` now supports narration-driven BGM ducking with FFmpeg `sidechaincompress`; original BGM, SHA-256 and mix parameters are preserved in the product ZIP/manifest/storyboard and checked by `verify_path`. Builder version 7.


## Local Cantonese TTS candidate

`canto-tts` 0.1.3 is the reviewed CPU/ONNX Hong Kong Cantonese engine. Muse installed it in `.muse-canto-tts-py314` without modifying global Python. The model repository `typangaa/canto-tts-nano` is pinned to commit `fe17418a293d9be85685ddf750b0cfef9c88abb4`; the downloaded bundle contains 47 files totaling 764,005,540 bytes. The adapter requires this local model and never downloads weights during synthesis.

The model has one neutral default voice without runtime gender/style controls. Do not market it as a selectable female voice. Upstream benchmark figures are not Muse measurements. PyPI lists a newer 0.1.4 release, but Muse remains on the inspected 0.1.3 wheel pending a deliberate review.

Upstream references:
- https://github.com/typangaa/canto-tts
- https://huggingface.co/typangaa/canto-tts-nano
- https://pypi.org/project/canto-tts/0.1.3/

## Real synthesis and packaging smoke test

- First real local synthesis succeeded offline using the pinned ONNX model. Example evaluation phrase produced a 48 kHz stereo PCM WAV; `data/narration/cantonese-smoke-test.wav` is 307,244 bytes.
- The first duration-filter attempt exposed a Windows cross-volume `os.replace` error because the upstream quality helper created candidate WAVs on C: while Muse wrote to D:. The adapter now keeps those temporary candidates on the output volume; the same real synthesis path then succeeded.
- A real TTS-to-product smoke test generated a five-second FFmpeg test-pattern MP4 (explicitly marked as a test fixture, not AI footage), synthesized a Cantonese voiceover, muxed the audio, and published a verified product ZIP. Result: `validated=true`, `video_metadata.has_audio=true`, synthesis `zh-HK` / 48 kHz stereo / 1.36 seconds. Product QA reported a sustained-silence review warning because the five-second fixture outlasted the short narration; the quality gate passed but `ready_to_publish=false`. This is a pipeline smoke test, not a finished creative video.
- The generated test assets live under `data/narration/`; the product is under `data/products/8db0ba4acf03cf50967988078de5ee93/022571eb69f121cb9834`.
- A real VideoProductBuilder sidechain smoke test used a synthetic 4-second video, voice WAV and looping BGM WAV; FFmpeg mixed them, full decode succeeded, product ZIP contained both source tracks, and `verify_path` confirmed the BGM SHA-256. This is synthetic toolchain evidence, not a real Cantonese narration or AI film.
## Installation and ASR status

- Core TTS dependencies and `faster-whisper` are installed in the project-local Python 3.14 target. The pinned Cantonese Whisper model download Job `wc_job_BFvgB6jBoFejfsaG` timed out after repeated peer disconnects and TLS handshake failures; the final `cts/model.bin` is absent and no ASR model readiness is claimed.
- Piper and ChatTTS launchers were found earlier, but no usable voice/model weights were present in their configured directories.
- No human listening review has been completed. The neutral default voice is not the requested mature female voice; alternate licensed Cantonese voices remain a separate research task.

## Acceptance gates before enabling automatic narration by default

1. Complete the pinned ASR model download and compare synthesized Cantonese against the source script, including colloquial phrases, names, numbers, English code-switching and homophones.
2. Build sentence/scene-level segments; derive subtitle cue times from actual audio frame counts rather than text length.
3. Keep duration/silence checks and LUFS/true-peak/sample-peak/RMS audio QA; voice-aware BGM sidechain mixing is now implemented. Human listening review remains required.
4. Mux the approved voice track and timed subtitles into a real generated AI video; verify full decode, audio presence, subtitle sync and ZIP integrity.
5. Disclose synthetic speech where viewers could mistake it for a human recording. Keep products in testing until editorial, rights, disclosure and platform review are complete.
## Verified versus pending

Real local Cantonese WAV synthesis and real FFmpeg video+voice product packaging have now been exercised. Unit tests and the full Muse suite pass, but speech intelligibility/accuracy has not yet been verified by ASR or human listening. The smoke-test clip is not a publish-ready AI video; automatic narration remains under evaluation until ASR, timing, mix, listening and real-footage acceptance gates pass.
The ASR/SRT implementation and packaging integration are covered by mocked tests; the real pinned Whisper model has not finished downloading, so no real transcript, subtitle timing or ASR accuracy result is claimed. See `docs/PROGRESS_B137_CANTONESE_ASR_PIPELINE.md` for this development slice.
Audio QA and BGM ducking implementation are recorded in `docs/PROGRESS_B138_VIDEO_AUDIO_QA.md` and `docs/PROGRESS_B139_VOICE_AWARE_BGM_DUCKING.md`. Precise clipping sample counts, human listening, real Cantonese ASR and real Wan-footage acceptance remain pending.