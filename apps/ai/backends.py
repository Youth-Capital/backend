"""Hosted-model backends for the chat.

The platform must not be tied to one vendor (промпт §26), so a backend is a
small interface with one method and the rest of the system talks to that. The
Anthropic implementation below is *a* backend, not *the* backend: adding
another is writing one class and one registry entry.

What a backend is allowed to do is deliberately narrow. It receives facts that
were already read from the database and is asked to **phrase** them. It never
computes a score, and it is told so in the system prompt, because the employer
sees the engine's number on the candidate list — an answer that contradicts it
is worse than no answer at all.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod

from django.utils.translation import get_language

logger = logging.getLogger(__name__)

#: Language name for the model, keyed by the request's active locale.
LANGUAGE_NAMES = {"uz": "Uzbek (latin script)", "ru": "Russian", "en": "English"}

SYSTEM_PROMPT = """\
You are the assistant inside Youth Capital, a platform that takes young \
people in Uzbekistan from education to employment.

You will be given FACTS read from the platform's database for the person \
asking. Answer their question using those facts.

Rules that matter more than being helpful:

1. Never invent or recompute a number. If a figure is not in FACTS, say you \
do not have it. The matching engine's numbers are what employers see; an \
answer that disagrees with them is worse than no answer.
2. Never reveal a candidate's name or personal details. Employers see identity \
only after a candidate applies.
3. You are not a doctor, lawyer or financial adviser. For questions in those \
areas, say plainly that this needs a specialist.
4. If the question has nothing to do with study, skills, careers or hiring, \
say so briefly and offer what you can help with instead.

Write in {language}. Be direct and concrete. Two to five sentences unless the \
person asked for detail. No preamble, no bullet lists unless enumerating.\
"""


class ChatBackend(ABC):
    """Turn a question plus facts into a sentence. Nothing else."""

    name: str = "abstract"

    def is_ready(self) -> bool:
        """Can this backend actually run right now?

        Checked before use so a missing key costs one boolean per message
        instead of an exception and a stack trace per message.
        """
        return True

    @abstractmethod
    def reply(self, *, question: str, facts: dict, history: list[dict]) -> str:
        """Return the assistant's text, or raise on failure.

        Raising is correct: the caller falls back to the rule-based answer,
        which is always available. Returning an apology string would replace a
        working answer with a broken one.
        """


class AnthropicChatBackend(ChatBackend):
    """Claude via the official SDK."""

    name = "anthropic"

    def __init__(self, config):
        self.config = config
        self.model = config.model or "claude-opus-5"
        self.max_tokens = config.max_tokens or 2048
        self.timeout = config.timeout_seconds or 30

    def _api_key(self) -> str | None:
        env_name = self.config.api_key_env_name
        return os.environ.get(env_name) if env_name else None

    def is_ready(self) -> bool:
        if not self._api_key():
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def reply(self, *, question: str, facts: dict, history: list[dict]) -> str:
        import anthropic

        key = self._api_key()
        if not key:
            raise RuntimeError(
                f"{self.config.api_key_env_name or 'API key env name'} is not set."
            )

        client = anthropic.Anthropic(api_key=key, timeout=float(self.timeout))
        language = LANGUAGE_NAMES.get(get_language() or "uz", LANGUAGE_NAMES["uz"])

        messages = list(history[-8:])
        messages.append(
            {
                "role": "user",
                "content": (
                    f"FACTS (read from the database, authoritative):\n"
                    f"{json.dumps(facts, ensure_ascii=False, indent=2, default=str)}\n\n"
                    f"QUESTION: {question}"
                ),
            }
        )

        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT.format(language=language),
            messages=messages,
        )

        if response.stop_reason == "refusal":
            raise RuntimeError("Model declined to answer.")

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not text:
            raise RuntimeError("Model returned no text.")
        return text


class UnsupportedBackend(ChatBackend):
    """A provider that is configured but has no implementation yet.

    Explicit rather than silent: selecting OpenAI in the admin and getting
    rule-based answers with no explanation would be a confusing hour.
    """

    def __init__(self, provider: str):
        self.name = provider.lower()
        self.provider = provider

    def is_ready(self) -> bool:
        return False

    def reply(self, *, question: str, facts: dict, history: list[dict]) -> str:
        raise NotImplementedError(
            f"No chat backend is implemented for provider {self.provider}."
        )


def get_chat_backend():
    """The configured backend, or None when the chat should stay rule-based."""
    from .models import AIProvider, AIProviderConfig

    config = AIProviderConfig.objects.filter(is_active=True).first()
    if config is None or config.provider == AIProvider.RULE_BASED:
        return None

    backend = (
        AnthropicChatBackend(config)
        if config.provider == AIProvider.ANTHROPIC
        else UnsupportedBackend(config.provider)
    )
    # A configured-but-unusable provider is the same as none: the rule-based
    # answer is already correct, and logging a failure per message would bury
    # the real errors.
    return backend if backend.is_ready() else None
