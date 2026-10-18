"""Single env load so health and the agent harness cannot disagree."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def openai_api_key() -> str:
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def openai_client_args() -> dict[str, object]:
    """Fail fast on 429 — do not retry into an nginx 504."""
    return {"api_key": openai_api_key(), "max_retries": 0, "timeout": 12.0}


def openai_classify_model() -> str:
    """Cheap model for scope JSON if set; else the same OpenAI model."""
    return (
        (os.environ.get("OPENAI_CLASSIFY_MODEL") or "").strip()
        or (os.environ.get("OPENAI_MODEL") or "").strip()
        or "gpt-4o-mini"
    )
