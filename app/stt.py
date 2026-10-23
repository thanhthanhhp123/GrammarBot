import asyncio

import httpx

from app.config import STT_BASE_URL

TIMEOUT = httpx.Timeout(60.0, connect=5.0)
TOTAL_SECONDS = 60.0
HEALTH_TIMEOUT = 2.0


class STTError(Exception):
    """Transcription failed. The message is safe to show to users."""

    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


async def is_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
            response = await client.get(f"{STT_BASE_URL}/")
        return response.is_success
    except httpx.HTTPError:
        return False


async def transcribe(audio: bytes, content_type: str) -> str:
    """Send audio to whisper-server and return the text. Raises STTError on any failure."""
    # whisper-server detects WAV/MP3 from the bytes, so the file name doesn't matter.
    files = {"file": ("audio", audio, content_type)}
    try:
        async with asyncio.timeout(TOTAL_SECONDS):
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(f"{STT_BASE_URL}/inference", files=files, data={"response_format": "json"})
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise STTError("The speech-to-text server is not reachable.") from e
    except (httpx.TimeoutException, TimeoutError) as e:
        raise STTError("The speech-to-text server took too long to answer.") from e
    except httpx.HTTPError as e:
        raise STTError("Lost connection to the speech-to-text server.") from e
    if response.status_code == 400:
        raise STTError("Could not read the audio. Send a WAV or MP3 file.", status_code=400)
    if response.is_error:
        raise STTError(f"The speech-to-text server returned an error ({response.status_code}).")
    return response.json()["text"].strip()
