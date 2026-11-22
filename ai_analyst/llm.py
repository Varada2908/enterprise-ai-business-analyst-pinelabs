"""Thin wrapper around the Gemini API for structured (JSON) responses.

Shared by the one-shot SQL generator and the multi-step investigator: one place handles the
API key, rate-limit retries, blocked/truncated responses and parsing into a pydantic model.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import config

T = TypeVar("T", bound=BaseModel)

BLOCKED_FINISH_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"}


class GenerationError(RuntimeError):
    """The model could not produce a usable response (blocked, truncation, API failure)."""


@dataclass
class LLMResponse:
    parsed: BaseModel
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    finish_reason: str | None = None


class GeminiJSONClient:
    """Calls Gemini with a system instruction and a response schema; returns the parsed model.

    The google-genai client is created on first use, so a missing key surfaces as a
    GenerationError at call time rather than at import/construction time.
    """

    def __init__(self, model: str = config.GEMINI_MODEL, client=None,
                 temperature: float = config.GEMINI_TEMPERATURE,
                 max_output_tokens: int = config.MAX_OUTPUT_TOKENS,
                 retries: int = config.RATE_LIMIT_RETRIES, backoff_s: float = config.RATE_LIMIT_BACKOFF_S):
        self._client = client
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.retries = retries
        self.backoff_s = backoff_s

    @property
    def client(self):
        if self._client is None:
            key = config.gemini_api_key()
            if not key:
                raise GenerationError("No Gemini API key found: set GEMINI_API_KEY in your environment "
                                      "or in ai_analyst/.env (free key: https://aistudio.google.com/apikey).")
            from google import genai
            self._client = genai.Client(api_key=key)
        return self._client

    def generate(self, system: str, contents, schema: type[T]) -> LLMResponse:
        """``contents`` is a user message string or a list of {"role", "parts"} turns."""
        response = self._request(system, contents, schema)

        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise GenerationError(f"Gemini blocked the request ({_enum_name(feedback.block_reason)}).")
        candidates = getattr(response, "candidates", None) or []
        finish = _enum_name(candidates[0].finish_reason) if candidates else None
        if finish == "MAX_TOKENS":
            raise GenerationError("The model's response was cut off (max output tokens).")
        if finish in BLOCKED_FINISH_REASONS:
            raise GenerationError(f"Gemini declined to answer this question ({finish}).")

        parsed = getattr(response, "parsed", None)
        text = getattr(response, "text", None) or ""
        if not isinstance(parsed, schema):
            try:
                parsed = schema.model_validate_json(text)
            except (ValidationError, ValueError) as e:
                raise GenerationError(f"The model did not return a valid {schema.__name__}.") from e
        if not text:
            text = parsed.model_dump_json()

        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            parsed=parsed,
            text=text,
            model=getattr(response, "model_version", None) or self.model,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
            finish_reason=finish,
        )

    def _request(self, system: str, contents, schema):
        from google.genai import errors, types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            response_mime_type="application/json",
            response_schema=schema,
        )
        for attempt in range(self.retries + 1):
            try:
                return self.client.models.generate_content(model=self.model, contents=contents, config=cfg)
            except errors.APIError as e:
                if e.code in (429, 500, 503) and attempt < self.retries:
                    time.sleep(self.backoff_s * (attempt + 1))
                    continue
                raise GenerationError(_describe_api_error(e, self.model)) from e
            except GenerationError:
                raise
            except Exception as e:  # network failures surface as httpx/requests exceptions
                raise GenerationError(f"Could not reach the Gemini API: {type(e).__name__}: {e}") from e


def _enum_name(value) -> str | None:
    return None if value is None else getattr(value, "name", str(value))


def _describe_api_error(e, model: str) -> str:
    msg = (getattr(e, "message", None) or str(e)).splitlines()[0]
    if e.code in (400, 401, 403) and "key" in msg.lower():
        return "Gemini API rejected the key: check GEMINI_API_KEY."
    if e.code == 404:
        return f"Model '{model}' is not available to this key: set GEMINI_MODEL to another model. ({msg[:160]})"
    if e.code == 429:
        return "Gemini free-tier rate limit or daily quota reached; wait a minute and try again."
    return f"Gemini API error {e.code}: {msg}"
