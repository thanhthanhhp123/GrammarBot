import secrets

from fastapi import Header, HTTPException

from app.config import API_KEY


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    # Compare bytes: compare_digest raises TypeError on non-ASCII str, which would be a 500.
    if x_api_key is None or not secrets.compare_digest(x_api_key.encode(), API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
