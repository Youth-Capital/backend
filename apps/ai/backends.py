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

from . import prompting
from .persona import LANGUAGE_NAMES, TASK_BASE, build_system_prompt

logger = logging.getLogger(__name__)


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

    def run_task(
        self,
        *,
        instructions: str,
        material: list[tuple[str, str]],
        question: str = "",
        user=None,
        max_tokens: int | None = None,
    ) -> str:
        """Work over course material with `instructions`; see AnthropicChatBackend."""
        raise NotImplementedError(f"{self.name} cannot run course-material tasks.")

    def structured(
        self,
        *,
        prompt: str,
        schema: dict,
        system: str = "",
        effort: str = "medium",
        material: list[tuple[str, str]] | None = None,
        user=None,
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

    # -- the one place a request is assembled and a reply is vetted ----------
    def _history(self, history: list[dict]) -> list[dict]:
        """Earlier turns, defused, starting on a user turn.

        Earlier turns are untrusted twice over: the person's messages, and the
        model's own earlier replies, which an injection may already have shaped.
        Both are defused so neither can carry a fence into this request.
        """
        turns = [
            {"role": turn["role"], "content": prompting.neutralize(str(turn["content"]))}
            for turn in history[-8:]
            if turn.get("content")
        ]
        while turns and turns[0]["role"] != "user":
            turns.pop(0)
        return turns

    def _send(
        self,
        *,
        system: str,
        blocks: list[str],
        history: list[dict] | None = None,
        max_tokens: int | None = None,
        effort: str = "low",
        output_format: dict | None = None,
    ) -> str:
        client = self._client()
        output_config = {"effort": effort}
        if output_format is not None:
            output_config["format"] = output_format

        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config=output_config,
            messages=[
                *(history or []),
                {
                    "role": "user",
                    "content": [{"type": "text", "text": block} for block in blocks],
                },
            ],
        )

        if response.stop_reason == "refusal":
            # Deliberately not routed to another model. The rule-based answer
            # the caller already has is grounded in this person's own database
            # rows, which is a better answer than a second model's guess.
            raise RuntimeError("Model declined to answer.")

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not text:
            raise RuntimeError("Model returned no text.")
        return text

    def _vet(self, text: str, *, system: str, envelope, sources: list[str], user=None) -> str:
        """Refuse a reply that recites its instructions or invents a link.

        Runs on every reply, whoever asked for it, because the output side is
        the one place the platform can check what the model actually did rather
        than what it was asked to do.
        """
        from .models import SafetyAction, SafetySeverity
        from .safety import record_event

        if prompting.leaks_instructions(text, system, envelope.canary):
            record_event(
                rule="prompt_leak", action=SafetyAction.BLOCKED,
                severity=SafetySeverity.HIGH, user=user,
            )
            raise prompting.OutputRejected("prompt_leak")

        links = prompting.unsourced_links(text, *sources)
        if links:
            record_event(
                rule="unsourced_link", action=SafetyAction.BLOCKED,
                severity=SafetySeverity.MEDIUM, user=user,
                details={"links": links[:5]},
            )
            raise prompting.OutputRejected("unsourced_link")
        return text

    # -- the three kinds of request -------------------------------------------
    def reply(
        self,
        *,
        question: str,
        facts: dict,
        history: list[dict],
        user=None,
        preferences: dict | None = None,
    ) -> str:
        """Phrase database facts in answer to somebody's question.

        The facts and the question travel as two fenced blocks, never as one
        string. They used to be one — `FACTS (...authoritative): ... QUESTION: ...`
        — and a question that brought its own FACTS paragraph was
        indistinguishable from the real one.
        """
        envelope = prompting.Envelope()
        system = prompting.compose(
            # Composed for this person: role, where their account actually is,
            # and any preference they set. See apps/ai/persona.py.
            build_system_prompt(user, language=self._language(), preferences=preferences),
            prompting.protocol(envelope.nonce, facts=True, question=True),
            prompting.confidentiality(envelope.canary),
        )
        text = self._send(
            system=system,
            blocks=[
                prompting.facts_block(facts, envelope.nonce),
                prompting.question_block(question, envelope.nonce),
            ],
            history=self._history(history),
        )
        return self._vet(
            text, system=system, envelope=envelope,
            sources=[prompting.json_fenced(facts)], user=user,
        )

    def run_task(
        self,
        *,
        instructions: str,
        material: list[tuple[str, str]],
        question: str = "",
        user=None,
        max_tokens: int | None = None,
    ) -> str:
        """Work over course text somebody else wrote: answer, summarise, quiz.

        The task's rules go into `system`; the course text goes into the user
        turn, one fenced block per source. That split is the whole defence
        against a lesson that tries to give orders: the author can write into
        the second channel and never into the first.
        """
        envelope = prompting.Envelope()
        system = prompting.compose(
            TASK_BASE,
            instructions,
            prompting.protocol(envelope.nonce, material=True, question=bool(question)),
            prompting.confidentiality(envelope.canary),
        )
        blocks = [prompting.material_block(label, text, envelope.nonce) for label, text in material]
        if question:
            blocks.append(prompting.question_block(question, envelope.nonce))

        text = self._send(system=system, blocks=blocks, max_tokens=max_tokens)
        return self._vet(
            text, system=system, envelope=envelope,
            sources=[body for _, body in material], user=user,
        )

    def structured(
        self,
        *,
        prompt: str,
        schema: dict,
        system: str = "",
        effort: str = "medium",
        material: list[tuple[str, str]] | None = None,
        user=None,
    ) -> dict:
        """JSON the API guarantees matches `schema`.

        With `material`, the instructions (`prompt`) move into `system` and the
        course text is fenced in the user turn, exactly as for `run_task` — a
        quiz written from a lesson is as open to a planted instruction as an
        answer is.
        """
        import json

        envelope = prompting.Envelope()
        if material:
            composed = prompting.compose(
                TASK_BASE,
                system,
                prompt,
                prompting.protocol(envelope.nonce, material=True),
                prompting.confidentiality(envelope.canary),
            )
            blocks = [prompting.material_block(label, text, envelope.nonce) for label, text in material]
            blocks.append("Write the requested output now, from the material above.")
        else:
            composed = prompting.compose(
                system or "Return only the requested JSON.",
                prompting.confidentiality(envelope.canary),
            )
            blocks = [prompt]

        text = self._send(
            system=composed,
            blocks=blocks,
            # Generous, because the caller is asking for a whole quiz and a
            # response cut off at the cap is a half-written question rather
            # than a short one. Well under the streaming threshold.
            max_tokens=8000,
            effort=effort,
            output_format={"type": "json_schema", "schema": schema},
        )
        # The format guarantee means the text is valid JSON matching the
        # schema -- no regex, no "find the first {".
        self._vet(
            text, system=composed, envelope=envelope,
            sources=[body for _, body in (material or [])] + [prompt], user=user,
        )
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
