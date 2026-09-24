"""The real Anthropic backend with the network swapped for a recorder.

Security tests on a model call have to look at the request that would really
be sent — which field the instructions went into, which block the data went
into — so they use `AnthropicChatBackend` itself and replace only the client.
Nothing here talks to Anthropic, and no key is needed.

`respond` is either a fixed reply or a function of the request, which is how a
test plays the part of a model that has been talked into misbehaving: echoing
its instructions, or relaying a planted link.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from apps.ai.backends import AnthropicChatBackend

NONCE = re.compile(r"_([0-9a-f]{12})\b")


class FakeMessages:
    def __init__(self, respond):
        self.calls: list[dict] = []
        self._respond = respond

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self._respond(kwargs) if callable(self._respond) else self._respond
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
        )


class CapturingBackend(AnthropicChatBackend):
    def __init__(self, respond="A plain, grounded answer."):
        super().__init__(
            SimpleNamespace(
                model="claude-opus-5",
                max_tokens=1024,
                timeout_seconds=30,
                api_key_env_name="YC_TEST_ANTHROPIC_KEY",
            )
        )
        self.fake = FakeMessages(respond)

    def is_ready(self) -> bool:
        return True

    def _client(self):
        return SimpleNamespace(messages=self.fake)

    @property
    def calls(self) -> list[dict]:
        return self.fake.calls


def last_user_blocks(call: dict) -> list[str]:
    """The text blocks of the final user turn, in order."""
    content = call["messages"][-1]["content"]
    if isinstance(content, str):
        return [content]
    return [block["text"] for block in content]


def request_nonce(call: dict) -> str:
    """The fence suffix this request's instructions named."""
    match = NONCE.search(call["system"])
    assert match, "the instructions do not name a fence"
    return match.group(1)


def everything_sent(call: dict) -> str:
    """System plus every message, flattened — for "is X anywhere" checks."""
    parts = [call.get("system", "")]
    for message in call["messages"]:
        content = message["content"]
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.extend(block.get("text", "") for block in content)
    return "\n".join(parts)
