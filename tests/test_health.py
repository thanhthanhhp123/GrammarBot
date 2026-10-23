import pytest
from fastapi.testclient import TestClient

from app import llm, stt
from app.config import LLM_MODEL
from app.main import app

client = TestClient(app)


def test_health_when_llm_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def up() -> bool:
        return True

    monkeypatch.setattr(llm, "is_reachable", up)
    monkeypatch.setattr(stt, "is_reachable", up)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": LLM_MODEL, "llm_reachable": True, "stt_reachable": True}


def test_health_when_llm_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "LLM_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setattr(stt, "STT_BASE_URL", "http://127.0.0.1:9")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["llm_reachable"] is False
    assert response.json()["stt_reachable"] is False
