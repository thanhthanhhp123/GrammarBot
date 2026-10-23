import asyncio
import json
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm, sessions
from app.main import app

client = TestClient(app)
AUTH = {"X-API-Key": "test-key"}


def fake_llm(monkeypatch: pytest.MonkeyPatch, deltas: list[str], error: str | None = None) -> list[Any]:
    """Make the LLM yield `deltas`, then raise LLMError(error) if given. Returns the messages it was called with."""
    calls: list[Any] = []

    async def stream_chat(messages: list[dict[str, str]]) -> AsyncIterator[str]:
        calls.append(messages)
        for delta in deltas:
            yield delta
        if error:
            raise llm.LLMError(error)

    monkeypatch.setattr(llm, "stream_chat", stream_chat)
    return calls


def sse_events(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


# --- /chat route (LLM mocked) ---


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": b"\xc3\xa9"}])
def test_bad_api_key_is_401(headers: dict[str, Any]) -> None:
    assert client.post("/chat", json={"message": "hi"}, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "body", [{}, {"message": "   "}, {"message": "x" * 2001}, {"message": "hi", "level": "expert"}]
)
def test_invalid_body_is_422(body: dict[str, Any]) -> None:
    assert client.post("/chat", json=body, headers=AUTH).status_code == 422


def test_answer_and_history(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_llm(monkeypatch, ["Use it ", "for experiences."])
    response = client.post("/chat", json={"message": "When do I use present perfect?"}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Use it for experiences."
    assert body["session_id"]

    client.post("/chat", json={"message": "More?", "session_id": body["session_id"], "level": "advanced"}, headers=AUTH)
    system, *rest = calls[1]
    assert system["role"] == "system" and "advanced" in system["content"]
    assert [m["content"] for m in rest] == ["When do I use present perfect?", "Use it for experiences.", "More?"]


def test_streaming_sends_deltas_then_done(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llm(monkeypatch, ["Hel", "lo"])
    response = client.post("/chat", json={"message": "hi", "session_id": "s1", "stream": True}, headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert sse_events(response.text) == [{"delta": "Hel"}, {"delta": "lo"}, {"done": True, "session_id": "s1"}]


@pytest.mark.parametrize("stream", [True, False])
def test_llm_down_is_503(monkeypatch: pytest.MonkeyPatch, stream: bool) -> None:
    fake_llm(monkeypatch, [], error="The language model server is not reachable.")
    response = client.post("/chat", json={"message": "hi", "stream": stream}, headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"detail": "The language model server is not reachable."}


def test_mid_stream_failure_sends_error_event_and_skips_history(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llm(monkeypatch, ["Hel"], error="The language model took too long to answer.")
    response = client.post("/chat", json={"message": "hi", "session_id": "s2", "stream": True}, headers=AUTH)
    assert sse_events(response.text) == [{"delta": "Hel"}, {"error": "The language model took too long to answer."}]
    assert sessions.get_history("s2") == []


# --- sessions ---


def test_history_keeps_last_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "MAX_HISTORY_TURNS", 2)
    for i in range(3):
        sessions.add_turn("s3", f"q{i}", f"a{i}")
    assert [m["content"] for m in sessions.get_history("s3")] == ["q1", "a1", "q2", "a2"]


def test_least_recent_session_is_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "_sessions", OrderedDict())
    monkeypatch.setattr(sessions, "MAX_SESSIONS", 2)
    for session_id in ["a", "b", "a", "c"]:
        sessions.add_turn(session_id, "q", "x")
    assert sessions.get_history("b") == []
    assert sessions.get_history("a") and sessions.get_history("c")


# --- llm client against canned HTTP responses ---


def drain_llm(monkeypatch: pytest.MonkeyPatch, response: httpx.Response | None = None) -> list[str]:
    """Run the real llm.stream_chat; if `response` is given, the LLM server returns it."""
    if response is not None:
        transport = httpx.MockTransport(lambda request: response)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))

    async def drain() -> list[str]:
        return [delta async for delta in llm.stream_chat([{"role": "user", "content": "hi"}])]

    return asyncio.run(drain())


def test_llm_parses_openai_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        ": keep-alive\n\n"
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    assert drain_llm(monkeypatch, httpx.Response(200, text=body)) == ["Hel", "lo"]


def test_llm_http_error_is_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(404, json={"error": {"message": 'model "x" not found'}})
    with pytest.raises(llm.LLMError, match='model "x" not found'):
        drain_llm(monkeypatch, response)


def test_llm_unreachable_is_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "LLM_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    with pytest.raises(llm.LLMError, match="not reachable"):
        drain_llm(monkeypatch)
