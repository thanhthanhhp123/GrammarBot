import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints

from app import llm, sessions
from app.config import LLM_MODEL
from app.prompts import system_prompt
from app.security import require_api_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # keep one log line per request
log = logging.getLogger("grammarbot")

app = FastAPI(title="Grammar Bot")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    model: str
    llm_reachable: bool


class ChatRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    session_id: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    stream: bool = False


class ChatResponse(BaseModel):
    answer: str
    session_id: str


def _log(session_id: str, level: str, started: float, status: int | str) -> None:
    latency = time.perf_counter() - started
    log.info("chat session=%s level=%s status=%s latency=%.2fs", session_id, level, status, latency)


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(model=LLM_MODEL, llm_reachable=await llm.is_reachable())


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
async def chat(req: ChatRequest) -> ChatResponse | StreamingResponse:
    session_id = req.session_id or uuid.uuid4().hex
    messages = [
        {"role": "system", "content": system_prompt(req.level)},
        *sessions.get_history(session_id),
        {"role": "user", "content": req.message},
    ]
    started = time.perf_counter()
    deltas = llm.stream_chat(messages)
    try:
        # Wait for the first token before answering, so an LLM failure is still a real 503.
        first = await anext(deltas, "")
        if not req.stream:
            answer = first + "".join([delta async for delta in deltas])
            sessions.add_turn(session_id, req.message, answer)
            _log(session_id, req.level, started, 200)
            return ChatResponse(answer=answer, session_id=session_id)
    except llm.LLMError as e:
        _log(session_id, req.level, started, 503)
        raise HTTPException(status_code=503, detail=str(e)) from e

    async def events() -> AsyncIterator[str]:
        parts = [first]
        status = "client-closed"
        try:
            if first:
                yield _sse({"delta": first})
            async for delta in deltas:
                parts.append(delta)
                yield _sse({"delta": delta})
            # Save history only for complete answers.
            sessions.add_turn(session_id, req.message, "".join(parts))
            status = "200"
            yield _sse({"done": True, "session_id": session_id})
        except llm.LLMError as e:
            status = "stream-error"
            yield _sse({"error": str(e)})
        finally:
            _log(session_id, req.level, started, status)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# Mounted last so the API routes above take precedence.
app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True), name="static")
