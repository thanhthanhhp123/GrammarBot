import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from app.config import LLM_BASE_URL, LLM_MODEL

TIMEOUT = httpx.Timeout(60.0, connect=5.0)
TOTAL_SECONDS = 60.0  # httpx's 60 s applies per read, so the whole answer gets its own deadline
MAX_TOKENS = 400  # an 8 GB M1 does 5-10 tok/s on a 4B model; this keeps most answers inside TOTAL_SECONDS
HEALTH_TIMEOUT = 2.0


class LLMError(Exception):
    """The LLM call failed. The message is safe to show to users."""


async def is_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
            response = await client.get(f"{LLM_BASE_URL}/models")
        return response.is_success
    except httpx.HTTPError:
        return False


async def stream_chat(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """Yield the answer as text deltas. Raises LLMError on any failure."""
    deadline = asyncio.get_running_loop().time() + TOTAL_SECONDS
    body = {"model": LLM_MODEL, "messages": messages, "stream": True, "max_tokens": MAX_TOKENS}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            request = client.build_request("POST", f"{LLM_BASE_URL}/chat/completions", json=body)
            async with asyncio.timeout_at(deadline):
                response = await client.send(request, stream=True)
            try:
                if response.is_error:
                    await response.aread()
                    raise LLMError(_upstream_error(response.text))
                lines = response.aiter_lines()
                while True:
                    # The deadline wraps each read only: a timeout must never span a yield.
                    async with asyncio.timeout_at(deadline):
                        line = await anext(lines, None)
                    if line is None:
                        return
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    chunk = json.loads(data)
                    if "error" in chunk:
                        raise LLMError(_upstream_error(data))
                    for choice in chunk.get("choices", []):
                        if text := (choice.get("delta") or {}).get("content"):
                            yield text
            finally:
                await response.aclose()
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise LLMError("The language model server is not reachable.") from e
    except (httpx.TimeoutException, TimeoutError) as e:
        raise LLMError("The language model took too long to answer.") from e
    except httpx.HTTPError as e:
        raise LLMError("Lost connection to the language model server.") from e


def _upstream_error(body: str) -> str:
    # Ollama and llama-server both send {"error": {"message": ...}}.
    try:
        error = json.loads(body)["error"]
        detail = error["message"] if isinstance(error, dict) else str(error)
    except (ValueError, KeyError, TypeError):
        detail = body[:200]
    return f"The language model server returned an error: {detail}"
