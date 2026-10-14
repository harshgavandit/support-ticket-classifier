"""Shared provider configuration for every LLM entry point."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def get_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "openai" if os.getenv("OPENAI_API_KEY") else "gemini" if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") else "openai").lower()
    if provider not in {"gemini", "openai"}:
        raise ValueError("LLM_PROVIDER must be gemini or openai")
    return provider


def get_default_model() -> str:
    model = os.getenv("DEFAULT_MODEL", "")
    if get_provider() == "gemini":
        return model if model.startswith("gemini-") else "gemini-3.1-flash-lite"
    return os.getenv("OPENAI_MODEL") or (model if model and not model.startswith("gemini-") else "gpt-4o-mini")


def llm_options(seed: int | None = None) -> dict:
    options = {"timeout": 45, "max_retries": 1}
    if get_provider() == "gemini":
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise ValueError("Set GEMINI_API_KEY in the project's .env file")
        options.update(api_key=key, base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    else:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError("Set OPENAI_API_KEY in the project's .env file")
        options["api_key"] = key
        if get_default_model().startswith("gpt-6"):
            # Luna supports Chat Completions tool calls with reasoning disabled.
            options["reasoning_effort"] = "none"
        elif seed is not None:
            options["seed"] = seed
    return options
