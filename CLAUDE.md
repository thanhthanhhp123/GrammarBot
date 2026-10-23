# Grammar Bot Demo

A small self-hosted service that answers English grammar questions. It runs on a MacBook and is shared with a partner over Tailscale so their edge devices can call it. This is a demo: keep it minimal, reliable, and easy to run.

## Goal and scope

**In scope (must work for the demo)**
- `GET /health` so the partner can confirm connectivity from an edge device.
- `POST /chat` that answers grammar questions, with streaming.
- A single static web page for typing questions in a browser.
- Exposure inside the tailnet only, via `tailscale serve`.

**Out of scope for now**
- RAG, vector databases, user accounts, Docker builds for edge (ARM64) hardware.
- `POST /stt` (speech to text) is phase 2. Do not start it until `/chat` is done and verified.

## Architecture

```
Edge device / browser
        │  HTTPS (tailnet only)
        ▼
tailscale serve  ──►  FastAPI app (127.0.0.1:8000)
                          │  OpenAI-compatible API
                          ▼
                 Local LLM server (Ollama or llama.cpp server, Metal GPU)
```

- The app talks to the LLM through an OpenAI-compatible endpoint, so Ollama and `llama-server` (llama.cpp) are interchangeable by changing `LLM_BASE_URL`.
- The app binds to `127.0.0.1` only. Tailscale is the only way in.

## Stack

- Python 3.11+, FastAPI, Uvicorn, Pydantic v2, `httpx` (async), `python-dotenv`, `pytest`.
- LLM server: llama.cpp `llama-server`, built from source in `~/.local/llama.cpp` (current Ollama releases need macOS 14+, and this Mac runs macOS 13). Ollama works too on a newer macOS.
- No other dependencies without asking first.

## Configuration (`.env`)

| Variable | Example | Meaning |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://127.0.0.1:8080/v1` | OpenAI-compatible endpoint (llama-server `:8080/v1`; Ollama `:11434/v1`) |
| `LLM_MODEL` | `qwen3-4b-instruct` | Model name as the LLM server knows it |
| `API_KEY` | random string | Required in the `X-API-Key` header for `/chat` |
| `MAX_HISTORY_TURNS` | `6` | Turns of history kept per session |
| `STT_BASE_URL` | `http://127.0.0.1:8081` | whisper-server (whisper.cpp) for `/stt` |

Model size by Mac RAM (quantized): 8 GB → 3–4B; 16 GB+ → 7–8B. This Mac (M1, 8 GB) runs Qwen3-4B-Instruct-2507 Q4_K_M at about 14 tok/s. Qwen2.5-3B was tested first and stated wrong grammar rules, so don't go below 4B. Use a non-thinking (instruct) model, or `<think>` text reaches users.

Commit `.env.example`, never `.env`.

## API contract

**`GET /health`** → `200 {"status": "ok", "model": "<LLM_MODEL>", "llm_reachable": true|false, "stt_reachable": true|false}`
No API key required. Must respond even if the LLM server is down (`llm_reachable: false`).

**`POST /chat`** (header `X-API-Key`)

Request:
```json
{"message": "When do I use present perfect?", "session_id": "abc123", "level": "beginner", "stream": true}
```
- `session_id` optional; if missing, generate one and return it.
- `message`: 1–2000 characters (whitespace trimmed).
- `level`: `beginner` | `intermediate` | `advanced`, default `beginner`.
- `stream` default `false`. `true` → Server-Sent Events: `data: {"delta": "..."}` chunks, then `data: {"done": true, "session_id": "..."}`. If the LLM fails after streaming has started, the last event is `data: {"error": "..."}` instead of `done`.
- `stream: false` → `{"answer": "...", "session_id": "..."}`.

Errors: `401` bad key, `422` invalid body, `503` LLM unreachable or failed before the first token (`{"detail": "<readable message>"}`, never a stack trace).

**`POST /stt`** (header `X-API-Key`): the raw audio file is the request body, `Content-Type: audio/wav` or `audio/mpeg`, max 5 MB → `{"text": "..."}` (`""` when there is no speech). Raw body instead of multipart keeps `python-multipart` out. Errors: `400` unreadable audio, `401`, `413` too large, `415` wrong type, `422` empty body, `503` whisper-server down or timed out. Clients chain `/stt` → `/chat` themselves.

## Bot behavior (system prompt)

- Answer only English grammar and usage questions. Politely redirect anything else in one sentence.
- Reply in the language of the question (Vietnamese question → Vietnamese explanation, English examples).
- Structure each answer: the rule, 2–3 examples, one common mistake (Vietnamese learners' mistakes where relevant).
- Adjust length and terminology to `level`. Beginner: short, no jargon.
- If unsure, say so instead of inventing a rule.

Keep the system prompt in `app/prompts.py`, not inline in route code.

## Project structure

```
app/
  main.py        # FastAPI app, routes, startup
  config.py      # settings from .env
  llm.py         # async client for the OpenAI-compatible endpoint (stream + non-stream)
  sessions.py    # in-memory session history (LRU dict, max 500 sessions), trimmed to MAX_HISTORY_TURNS
  prompts.py     # system prompt builder by level
  security.py    # X-API-Key check
  stt.py         # async client for whisper-server /inference
static/
  index.html     # single page: input, level picker, mic button (records → 16 kHz WAV → /stt → /chat), streamed answer
tests/
  test_health.py
  test_chat.py   # LLM mocked
  test_stt.py    # whisper mocked
conftest.py      # sets a test API_KEY, puts the root on sys.path
.env.example
.gitignore
requirements.txt
README.md
```

In-memory sessions are fine for the demo; a restart clears history. Run a single uvicorn worker (never `--workers N`).

## Commands

```bash
# setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit

# LLM server (llama.cpp, Metal). mmap+mlock keeps the weights in RAM; without it, memory pressure halves the speed.
~/.local/llama.cpp/build/bin/llama-server -m ~/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8080 -c 8192 -ngl 99 --alias qwen3-4b-instruct --load-mode mmap+mlock
# The llama.cpp source carries a one-line patch: macOS 13 can't do Metal's async tensor read, so
# ggml/src/ggml-metal/ggml-metal.cpp sets .get_tensor_async = NULL (grep "grammarbot patch"). Re-apply it after
# updating the source, then: cmake --build ~/.local/llama.cpp/build --target llama-server -j

# speech to text (whisper.cpp v1.9.5, Metal, same "grammarbot patch" in its ggml copy).
# large-v3-turbo q5_0 handles Vietnamese well; a short clip takes ~5-8 s on this Mac.
~/.local/whisper.cpp/build/bin/whisper-server -m ~/models/ggml-large-v3-turbo-q5_0.bin \
  --host 127.0.0.1 --port 8081 -l auto -nt --vad -vm ~/models/ggml-silero-v6.2.0.bin
# --vad (Silero) skips non-speech; without it Whisper turns silence into "you" or "."
# rebuild: cmake --build ~/.local/whisper.cpp/build --target whisper-server -j

# run app
uvicorn app.main:app --host 127.0.0.1 --port 8000

# tests
pytest -q

# expose inside the tailnet (never use `tailscale funnel`: it makes the service public)
tailscale serve --bg 8000

# keep the Mac awake during the demo
caffeinate -dims
```

## Conventions

- Async everywhere on the request path; no blocking calls inside routes.
- Type hints on all functions; Pydantic models for every request and response.
- Timeouts on every LLM call (connect 5 s, total 60 s). On timeout return `503`, never hang.
- Log one line per request: session id, level, latency, status. Never log the API key.
- Keep files small and readable; no premature abstractions.

## Definition of done (verify before saying it works)

1. `pytest -q` passes.
2. `curl http://127.0.0.1:8000/health` returns `ok`.
3. `curl -N` a streaming `/chat` request and see tokens arrive incrementally.
4. Stop the LLM server: `/health` reports `llm_reachable: false`, `/chat` returns `503` with a readable message.
5. The web page streams an answer in the browser.
6. From another device in the tailnet, `https://<mac-name>.<tailnet>.ts.net/health` responds.

## README for the partner

Write `README.md` with: the tailnet URL placeholder, how to get the API key from us, `curl` examples for `/health` and `/chat` (streaming and non-streaming), and the request/response formats above.

## Phase 2

- Done: `POST /stt` via whisper.cpp `whisper-server` (see API contract). Chaining is left to clients.
- Possible speed-up: per-request `audio_ctx=512` cut a 7.5 s clip from ~8 s to ~2.4 s, but needs the clip length (stdlib `wave` for WAV only) and 256 made the model repeat itself. Not done; timings on this RAM-starved Mac were too noisy to trust.
- Later: RAG over curated grammar notes, Docker image for the partner's edge hardware.