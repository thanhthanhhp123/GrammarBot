# Grammar Bot

A small service that answers English grammar questions in English or Vietnamese, and can turn a spoken question into text first. It runs on our Mac and is reachable only inside our Tailscale network (tailnet).

**Base URL:** `https://<mac-name>.<tailnet>.ts.net` (we'll send you the exact name)

## Access

1. Your device must be in our tailnet, or we must share the Mac's node with your Tailscale account. Without that, the URL won't resolve.
2. `POST /chat` and `POST /stt` need an API key in the `X-API-Key` header. We'll send it to you privately (not by email or in a shared channel). Don't put it in code that's committed to a repo.

In the examples below, set these first:

```bash
BASE=https://<mac-name>.<tailnet>.ts.net
KEY=<the key we sent you>
```

## `GET /health`

Checks that you can reach the service. No API key needed.

```bash
curl $BASE/health
```

```json
{"status": "ok", "model": "qwen3-4b-instruct", "llm_reachable": true, "stt_reachable": true}
```

`llm_reachable: false` means the service is up but its language model is down, so `/chat` will return `503` until it's back. `stt_reachable: false` means the same for speech-to-text and `/stt`.

## `POST /chat`

Request body:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `message` | string | yes | 1–2000 characters |
| `session_id` | string | no | Up to 64 characters. Omit it on the first message; send back the one you get to continue the conversation |
| `level` | string | no | `beginner` (default), `intermediate`, or `advanced` |
| `stream` | bool | no | `false` (default) returns one JSON answer; `true` streams the answer as it's generated |

The bot answers in the language of the question (a Vietnamese question gets a Vietnamese explanation with English examples) and only answers English grammar questions.

### Non-streaming

```bash
curl $BASE/chat \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"message": "When do I use present perfect?", "level": "beginner"}'
```

```json
{"answer": "1. Use the present perfect ...", "session_id": "3f2a9c..."}
```

Follow-up in the same conversation:

```bash
curl $BASE/chat \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"message": "Give me more examples", "session_id": "3f2a9c..."}'
```

### Streaming (Server-Sent Events)

```bash
curl -N $BASE/chat \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"message": "Khi nào dùng thì hiện tại hoàn thành?", "stream": true}'
```

The response is `text/event-stream`. Each event is one `data:` line with a JSON object, followed by a blank line:

```
data: {"delta": "1. Thì hiện tại hoàn thành"}

data: {"delta": " dùng khi..."}

data: {"done": true, "session_id": "3f2a9c..."}
```

Join the `delta` strings to build the answer. The last event is either `{"done": true, "session_id": ...}` or, if the model fails partway through, `{"error": "<message>"}`.

## `POST /stt`

Turns a short spoken question (WAV or MP3, English or Vietnamese) into text. Send the audio file itself as the request body, with `Content-Type: audio/wav` or `audio/mpeg`:

```bash
curl $BASE/stt \
  -H "X-API-Key: $KEY" -H "Content-Type: audio/wav" \
  --data-binary @question.wav
```

```json
{"text": "Khi nào dùng thì hiện tại hoàn thành?"}
```

- Any sample rate, mono or stereo. The language is detected automatically.
- Keep clips under about 30 seconds (5 MB max). A short clip takes about 5–8 seconds to transcribe.
- Use `--data-binary`, not `-d` or `-F`: the body must be the raw file, not a form.

To ask by voice, send the `text` you get back as the `message` of a `POST /chat` request.

## Errors

Errors are JSON with a readable `detail`:

| Status | Meaning |
| --- | --- |
| `400` | `/stt`: the body isn't readable WAV or MP3 audio |
| `401` | Missing or wrong `X-API-Key` |
| `413` | `/stt`: audio larger than 5 MB |
| `415` | `/stt`: `Content-Type` isn't `audio/wav` or `audio/mpeg` |
| `422` | Invalid request body (for example empty `message`, unknown `level`, empty audio) |
| `503` | The language model or speech-to-text server is down or timed out. Try again shortly |

```json
{"detail": "The language model server is not reachable."}
```

Answers usually take a few seconds; requests time out after 60 seconds. Conversation history is kept in memory for the last 6 exchanges and is cleared when the service restarts.

## Web page

Open `$BASE/` in a browser on a tailnet device for a simple chat page. Enter the API key once; it's remembered in that browser.

---

## Running it (host side)

One-time setup:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set API_KEY; LLM_BASE_URL matches the LLM server below
```

Each demo, in four terminals:

```bash
# 1. LLM server (llama.cpp, Metal GPU)
~/.local/llama.cpp/build/bin/llama-server -m ~/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8080 -c 8192 -ngl 99 --alias qwen3-4b-instruct --load-mode mmap+mlock

# 2. Speech-to-text server (whisper.cpp, Metal GPU)
~/.local/whisper.cpp/build/bin/whisper-server -m ~/models/ggml-large-v3-turbo-q5_0.bin \
  --host 127.0.0.1 --port 8081 -l auto -nt

# 3. App
source .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8000

# 4. Tailnet exposure + keep the Mac awake (keep the lid open and the charger in)
tailscale serve --bg 8000   # once; the config survives restarts
caffeinate -dims
```

Never use `tailscale funnel`: it makes the service public.

Before the demo, quit Zoom, Discord and extra browser tabs. On an 8 GB Mac the model competes with them for RAM: with them open it generates about 5 tok/s, without them about 10 tok/s.

One-time Tailscale setup: install the app (`brew install --cask tailscale-app`, asks for your password), open it and sign in, then in the admin console under **DNS** turn on **MagicDNS** and **HTTPS Certificates**. `tailscale serve status` prints the URL to send to the partner.
