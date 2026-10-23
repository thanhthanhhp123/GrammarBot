import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, stt
from app.main import app

client = TestClient(app)
AUTH = {"X-API-Key": "test-key"}
WAV = {**AUTH, "Content-Type": "audio/wav"}


def fake_stt(monkeypatch: pytest.MonkeyPatch, text: str = "", error: stt.STTError | None = None) -> list[Any]:
    """Replace transcription with a canned result. Returns the (audio, content_type) calls."""
    calls: list[Any] = []

    async def transcribe(audio: bytes, content_type: str) -> str:
        calls.append((audio, content_type))
        if error:
            raise error
        return text

    monkeypatch.setattr(stt, "transcribe", transcribe)
    return calls


# --- /stt route (whisper mocked) ---


def test_transcribes_audio_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_stt(monkeypatch, text="When do I use present perfect?")
    response = client.post("/stt", content=b"RIFF...", headers={**AUTH, "Content-Type": "audio/mpeg"})
    assert response.status_code == 200
    assert response.json() == {"text": "When do I use present perfect?"}
    assert calls == [(b"RIFF...", "audio/mpeg")]


def test_missing_key_is_401() -> None:
    assert client.post("/stt", content=b"x", headers={"Content-Type": "audio/wav"}).status_code == 401


@pytest.mark.parametrize("content_type", ["application/x-www-form-urlencoded", "multipart/form-data", "audio/ogg"])
def test_unsupported_type_is_415(content_type: str) -> None:
    assert client.post("/stt", content=b"x", headers={**AUTH, "Content-Type": content_type}).status_code == 415


def test_too_large_is_413(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_AUDIO_BYTES", 10)
    assert client.post("/stt", content=b"x" * 11, headers=WAV).status_code == 413


def test_empty_body_is_422() -> None:
    assert client.post("/stt", content=b"", headers=WAV).status_code == 422


@pytest.mark.parametrize("status_code", [400, 503])
def test_stt_errors_are_readable(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    fake_stt(monkeypatch, error=stt.STTError("Readable message.", status_code=status_code))
    response = client.post("/stt", content=b"x", headers=WAV)
    assert response.status_code == status_code
    assert response.json() == {"detail": "Readable message."}


# --- stt client against canned whisper-server responses ---


def run_transcribe(monkeypatch: pytest.MonkeyPatch, response: httpx.Response | None = None) -> str:
    if response is not None:
        transport = httpx.MockTransport(lambda request: response)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    return asyncio.run(stt.transcribe(b"audio", "audio/wav"))


def test_client_returns_stripped_text(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_transcribe(monkeypatch, httpx.Response(200, json={"text": " Hello there.\n"})) == "Hello there."


def test_client_maps_unreadable_audio_to_400(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(stt.STTError) as error:
        run_transcribe(monkeypatch, httpx.Response(400, json={"error": "failed to read audio data"}))
    assert error.value.status_code == 400


def test_client_unreachable_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt, "STT_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    with pytest.raises(stt.STTError, match="not reachable") as error:
        run_transcribe(monkeypatch)
    assert error.value.status_code == 503
