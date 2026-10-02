"""Chat model construction. Models are named by "provider:model" specs and stay configurable."""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.config.settings import Settings

# Providers recognised as a "provider:" prefix. Anything else is a model name for the default provider
# (model names may themselves contain ":", e.g. Ollama tags).
KNOWN_PROVIDERS = {
    "openai",
    "anthropic",
    "azure_openai",
    "google_genai",
    "google_vertexai",
    "bedrock_converse",
    "ollama",
    "groq",
    "mistralai",
    "deepseek",
    "xai",
    "fireworks",
    "together",
}
PROVIDER_KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

ANTHROPIC_MAX_TOKENS = 64_000
# Lets requests carry history our harness has edited (compaction, per-call system prompt, model switches):
# thinking blocks whose prefix no longer matches are dropped instead of failing the request.
ANTHROPIC_BINDING_BETA = "thinking-binding-controls-2026-08-01"
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ConfigurationError(Exception):
    pass


def parse_spec(spec: str, default_provider: str = "openai") -> tuple[str, str]:
    provider, sep, model = spec.partition(":")
    if sep and provider in KNOWN_PROVIDERS and model:
        return provider, model
    return default_provider, spec


def provider_of(spec: str) -> str:
    return parse_spec(spec)[0]


def configured_providers() -> list[str]:
    return [p for p, key in PROVIDER_KEYS.items() if os.environ.get(key)]


def anthropic_kwargs(settings: Settings) -> dict:
    """Request settings for Claude models (see the Claude API reference):
    no sampling parameters, adaptive thinking with effort as the depth control, large max_tokens with
    streaming, tolerant thinking-block binding, and server-side refusal fallback unless disabled."""
    betas = [ANTHROPIC_BINDING_BETA]
    model_kwargs: dict = {}
    if settings.anthropic_fallbacks.lower() != "off":
        betas.append(ANTHROPIC_FALLBACK_BETA)
        model_kwargs["fallbacks"] = settings.anthropic_fallbacks
    return {
        "max_tokens": ANTHROPIC_MAX_TOKENS,
        "streaming": True,
        "betas": betas,
        "thinking": {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
        "output_config": {"effort": settings.model_effort},
        "model_kwargs": model_kwargs,
    }


def build_model(settings: Settings, spec: str | None = None) -> BaseChatModel:
    if settings.fake_model_script:
        return ScriptedChatModel.from_file(settings.fake_model_script)
    provider, model = parse_spec(spec or settings.default_model_spec, settings.model_provider)
    key = PROVIDER_KEYS.get(provider)
    if key and not os.environ.get(key):
        raise ConfigurationError(f"{key} is not set. Add it to agent/.env or the environment to use {provider}:{model}.")
    kwargs: dict = {}
    if provider == "openai":
        kwargs["stream_usage"] = True
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
    elif provider == "anthropic":
        kwargs.update(anthropic_kwargs(settings))
    return init_chat_model(model, model_provider=provider, **kwargs)
