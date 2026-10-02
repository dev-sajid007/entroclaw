"""Chat model construction. The model name and provider stay configurable."""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.config.settings import Settings


class ConfigurationError(Exception):
    pass


def build_model(settings: Settings) -> BaseChatModel:
    if settings.fake_model_script:
        return ScriptedChatModel.from_file(settings.fake_model_script)
    kwargs: dict = {}
    if settings.model_provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise ConfigurationError("OPENAI_API_KEY is not set. Add it to agent/.env or the environment.")
        kwargs["stream_usage"] = True
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
    return init_chat_model(settings.model, model_provider=settings.model_provider, **kwargs)
