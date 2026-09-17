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

from .persona import LANGUAGE_NAMES, build_system_prompt


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
    def reply(
        self,
        *,
        question: str,
        facts: dict,
        history: list[dict],
        user=None,
        preferences: dict | None = None,
    ) -> str:
        """Return the assistant's text, or raise on failure.

        Raising is correct: the caller falls back to the rule-based answer,
        which is always available. Returning an apology string would replace a
        working answer with a broken one.

        `user` and `preferences` are what make the answer this person's rather
        than anyone's. Both are optional so the internal callers that phrase a
        lesson recap -- where there is no person being addressed -- do not have
        to invent one.
        """

    def structured(
        self,
        *,
        prompt: str,
        schema: dict,
        system: str = "",
        effort: str = "medium",
    ) -> dict:
        """Return a dict the API guaranteed matches `schema`.

        Separate from `reply` because the failure modes are different. A reply
        that comes back slightly odd is still an answer; a generated quiz whose
        `answer` field is a string instead of an index is a lesson that marks
        every learner wrong. The schema is enforced by the API, so this either
        returns valid data or raises.
        """
        raise NotImplementedError(
            f"{self.name} cannot produce structured output."
        )


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

    def _client(self):
        import anthropic

        key = self._api_key()
        if not key:
            raise RuntimeError(
                f"{self.config.api_key_env_name or 'API key env name'} is not set."
            )
        return anthropic.Anthropic(api_key=key, timeout=float(self.timeout))

    @staticmethod
    def _language() -> str:
        return LANGUAGE_NAMES.get(get_language() or "uz", LANGUAGE_NAMES["uz"])

    def reply(
        self,
        *,
        question: str,
        facts: dict,
        history: list[dict],
        user=None,
        preferences: dict | None = None,
    ) -> str:
        client = self._client()
        language = self._language()

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
            # Composed for this person: role, where their account actually is,
            # and any preference they set. See apps/ai/persona.py.
            system=build_system_prompt(
                user, language=language, preferences=preferences
            ),
            # Adaptive rather than off. The work here is small -- phrase facts
            # somebody else read -- so effort stays low: this is the cheap,
            # high-volume path, and the quality that matters is coming from
            # the facts, not from deliberation about them.
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            messages=messages,
        )

        if response.stop_reason == "refusal":
            # Deliberately not routed to another model. The rule-based answer
            # the caller already has is grounded in this person's own database
            # rows, which is a better answer than a second model's guess --
            # the fallback here is *more* correct than the thing it replaces,
            # which is not usually true of a refusal fallback.
            raise RuntimeError("Model declined to answer.")

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not text:
            raise RuntimeError("Model returned no text.")
        return text

    def structured(
        self,
        *,
        prompt: str,
        schema: dict,
        system: str = "",
        effort: str = "medium",
    ) -> dict:
        import json

        client = self._client()
        response = client.messages.create(
            model=self.model,
            # Generous, because the caller is asking for a whole quiz and a
            # response cut off at the cap is a half-written question rather
            # than a short one. Well under the streaming threshold.
            max_tokens=8000,
            system=system or "Return only the requested JSON.",
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": prompt}],
        )

        if response.stop_reason == "refusal":
            raise RuntimeError("Model declined to produce the requested output.")

        # The format guarantee means the first text block is valid JSON
        # matching the schema -- no regex, no "find the first {".
        text = next(
            (block.text for block in response.content if block.type == "text"), ""
        )
        if not text:
            raise RuntimeError("Model returned no structured output.")
        return json.loads(text)


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

    def reply(
        self,
        *,
        question: str,
        facts: dict,
        history: list[dict],
        user=None,
        preferences: dict | None = None,
    ) -> str:
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
