# Optional Gemma GGUF intent assistance

Gemma is disabled by default and is not a Commander, planner, permission authority, or worker.
The deterministic intent rules run first. Gemma is considered only when those rules return
unknown and the operator explicitly enables it. A valid answer can suggest only an existing
Muse domain/capability pair; the capability registry, review gates, and approval rules remain
authoritative. Errors, invalid JSON, unsupported pairs, low confidence, missing files, and
timeouts return the original deterministic result.

## LM Studio (recommended when the model is already loaded)

Set these variables in the Muse process environment:

- MUSE_GEMMA_ENABLED=1
- MUSE_GEMMA_BACKEND=lmstudio
- MUSE_GEMMA_LMSTUDIO_URL=http://127.0.0.1:1234/v1
- MUSE_GEMMA_LMSTUDIO_MODEL — optional; omit to use the first model returned by /v1/models.
- MUSE_GEMMA_TIMEOUT_SECONDS=20

In LM Studio, open **Model Server**, start the local server, and confirm the server is listening on port 1234. Keep the selected model loaded. Muse calls only the loopback OpenAI-compatible API and never unloads the model from LM Studio.

## Triggering from ChatGPT

The project includes `src/muse/chatgpt_bridge.py`, a narrow CLI invoked by ChatGPT through the existing WebCodex Windows Runner. Use the WebCodex `run_process` callable with executable `py`, args `['-3', 'src/muse/chatgpt_bridge.py', 'classify', '--request', 'Write a Python function to sort a list.']`, and cwd `.`. The CLI enables the local LM Studio backend for that process, enforces loopback-only access, and returns an advisory classification. It does not execute tools or grant permissions.

For durable task intake, use the `submit` subcommand. It creates a ticket in the existing Muse TaskQueue with `waiting_approval` status. The separate `approve` subcommand records a named human decision and moves it to `queued`; it does not dispatch the ticket. Muse Commander remains the only scheduler.


## Direct GGUF backend

- MUSE_GEMMA_ENABLED=1
- MUSE_GEMMA_BACKEND=gguf (default)
- MUSE_GEMMA_MODEL_PATH=D:\\AI\\models\\<your-downloaded-model>.gguf
- MUSE_GEMMA_TIMEOUT_SECONDS=20
- MUSE_GEMMA_IDLE_UNLOAD_SECONDS=120
- MUSE_GEMMA_CONTEXT=2048
- MUSE_GEMMA_GPU_LAYERS=0 — conservative CPU default.

Install llama-cpp-python separately only after reviewing the appropriate Windows/AMD build.
The adapter selects llama.cpp's Gemma 4 chat format explicitly (gemma4); use a current llama-cpp-python build that includes Gemma 4 support.
Muse does not download models or install native dependencies. Inference uses a bounded daemon
thread; a timed-out generation is allowed to wind down safely, and concurrent requests fail
closed while it is still active. Logs contain only outcome, error class, and duration, never
the request or model response. unload_gemma_intent_model() is available for explicit release.
