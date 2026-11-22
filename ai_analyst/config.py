"""Settings for the AI analyst's LLM provider (Google Gemini).

Values come from environment variables. If an ``ai_analyst/.env`` file exists, it is loaded
first, so you can keep the key in that file instead of in your shell (variables already set in
the environment take priority). Copy ``.env.example`` to ``.env`` and fill it in.

    GEMINI_API_KEY      required; free key from https://aistudio.google.com/apikey
    GEMINI_MODEL        optional; default gemini-3.5-flash (on the free tier)
    GEMINI_TEMPERATURE  optional; default 0 (deterministic SQL)
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"


def _load_env_file(path: Path = ENV_FILE) -> None:
    """Minimal KEY=VALUE loader (no extra dependency). Never overrides real environment variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


_load_env_file()

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_TEMPERATURE = float(os.environ.get("GEMINI_TEMPERATURE", "0"))
# Gemini counts its internal "thinking" against this budget, so keep it well above the JSON size.
MAX_OUTPUT_TOKENS = 32768
# Free-tier keys have low per-minute limits; on HTTP 429/503 wait and retry this many times.
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_S = 15.0


def gemini_api_key() -> str | None:
    """The Gemini key, accepting the SDK's alternative name GOOGLE_API_KEY too."""
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
