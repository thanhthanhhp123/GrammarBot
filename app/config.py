import os

from dotenv import load_dotenv

load_dotenv()

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:3b")
API_KEY = os.environ.get("API_KEY", "")
MAX_HISTORY_TURNS = int(os.environ.get("MAX_HISTORY_TURNS", "6"))

if not API_KEY:
    raise RuntimeError("API_KEY is not set. Copy .env.example to .env and set it.")
