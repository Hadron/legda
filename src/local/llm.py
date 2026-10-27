"""Local model configuration (throw away)."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

DEFAULT_MODEL = "Qwen/Qwen3.8-27B"
DEFAULT_BASE_URL = "http://konishi.lan:8080/v1"


def get_reconcile_model(
    model_name: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    temperature: float = 0.0,
    timeout: float = 900.0,
    max_retries: int = 2,
    max_tokens: int | None = 32768,
):
    """Model client for the reconciliation step.

    Thinking stays ON (the merge judgment benefits from it) and structured
    output goes through function calling (see reconcile_provisions). The
    completion cap is generous for a merge, which converges in a few
    thousand tokens, but bounded so a pathological spiral fails and retries
    quickly instead of burning the endpoint.
    """
    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key="local",
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        max_tokens=max_tokens,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    )


def get_model(
    model_name: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    temperature: float = 0.0,
    timeout: float = 900.0,
    max_retries: int = 2,
    max_tokens: int | None = 32768,
):
    """A ChatOpenAI-compatible client pointed at the local endpoint.

    ``timeout`` bounds each request so a stuck generation cannot hang a
    worker indefinitely; ``max_retries`` bounds transient retries;
    ``max_tokens`` caps the completion (reasoning + content) per request —
    set with headroom for thinking models and large structured outputs.
    """
    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key="local",
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        max_tokens=max_tokens,
    )
