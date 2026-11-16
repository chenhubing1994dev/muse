# QA Report

- File: 火种不灭.mp4
- SHA-256: f3850405c754b031c63a641908e68b3b1156e42ab02095e00b0afbd72f8e5b5d
- Size: 21.99 MiB
- Duration: 45.000s

## Checks

- PASS — **MP4 exists**: 22.0 MiB
- PASS — **Video dimensions**: 1080x1920
- PASS — **Frame rate**: 24/1
- PASS — **Duration**: 45.000s
- PASS — **Audio stream**: {"codec_name": "aac", "sample_rate": "48000", "channels": 2}
- PASS — **Full decode**: FFmpeg decoded the full file
- PASS — **Integrated loudness**: measured -14.35 LUFS; target -14 LUFS
- PASS — **Black-frame scan**: 0 black segments detected (dark opening is intentional)
- PASS — **Cantonese subtitle file**: 7 cues

## Known limits
- Automatic Cantonese speech-to-text verification was not run because faster-whisper was not installed in the Lemo environment; the voice was generated with the explicit zh-HK-HiuMaanNeural Cantonese voice and the source lines are preserved in voice/lines.json.
- The film is original procedural animation, not Wan-generated footage. The Wan/ComfyUI path was unavailable at render time.
