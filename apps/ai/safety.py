"""Safety layer for AI output (TZ §13).

Three obligations from the TZ, in order of seriousness:

1. The platform serves minors, so harmful, discriminatory or dangerous content
   must be blocked, not merely discouraged.
2. AI must never issue a final medical or legal verdict — it hands off to a
   qualified human instead.
3. Every intervention is recorded, so the behaviour is auditable rather than
   invisible.

This is a deterministic pre/post filter. It is not a substitute for a provider's
own safeguards; it is the layer we control and can prove the behaviour of.
"""

from __future__ import annotations

import re

from .models import AISafetyEvent, SafetyAction, SafetySeverity

#: Topics where the correct answer is "talk to a qualified person".
ESCALATION_PATTERNS = {
    # Spelled out per language rather than trusting one stem to cover three.
    # The Russian half had only "самоубийство" and a bare "тревож", which
    # inverted the net: ordinary interview nerves — "меня тревожит
    # собеседование" — were escalated to a doctor, while "у меня депрессия"
    # went straight through to the model. The platform serves minors, so that
    # second case is the one this layer exists for. The noun forms escalate,
    # the verb does not.
    "medical": re.compile(
        r"\b(diagnos\w*|tashxis|диагноз|"
        r"depress\w*|депресс\w*|ruhiy tushkunlik|"
        r"suicid\w*|suitsid\w*|суицид\w*|o'z joniga|самоубий\w*|"
        r"anxiety|trevoga|xavotir buzilish\w*|"
        r"тревожност\w*|тревожн\w+ расстройств\w*|"
        # Trailing \w* everywhere a form can grow: the closing \b sits after
        # the whole group, so a bare "panic attack" misses "panic attacks",
        # and Uzbek agglutinates far more than that.
        r"panic attack\w*|vahima xuruj\w*|паническ\w+ атак\w*|"
        r"prescription|retsept|рецепт|dori|лекарств\w*)\b",
        re.IGNORECASE,
    ),
    "legal": re.compile(
        r"\b(sud|суд|lawsuit|iddao|иск\b|criminal|jinoyat|уголовн\w*|"
        r"contract dispute|shartnoma nizosi|юридическ\w* заключени\w*)\b",
        re.IGNORECASE,
    ),
    "financial_advice": re.compile(
        r"\b(guaranteed return|kafolatlangan daromad|гарантированн\w* доход|"
        r"invest all|barcha pulingizni|вложить все)\b",
        re.IGNORECASE,
    ),
}

#: Content that must never be produced or relayed.
BLOCK_PATTERNS = {
    "self_harm": re.compile(
        r"\b(kill yourself|o'zingni o'ldir|убей себя|self-harm)\b", re.IGNORECASE
    ),
    "discrimination": re.compile(
        r"\b(only men|only women|faqat erkaklar|faqat ayollar|только мужчин\w*|"
        r"только женщин\w*|no disabled|nogironlar kerak emas)\b",
        re.IGNORECASE,
    ),
    "illegal": re.compile(
        r"\b(fake diploma|soxta diplom|поддельн\w* диплом|bribe|pora|взятк\w*)\b",
        re.IGNORECASE,
    ),
}


class SafetyVerdict:
    def __init__(
        self,
        *,
        allowed: bool,
        action: str | None = None,
        rule: str = "",
        severity: str = SafetySeverity.MEDIUM,
        message: str = "",
        escalate_to: str = "",
    ):
        self.allowed = allowed
        self.action = action
        self.rule = rule
        self.severity = severity
        self.message = message
        self.escalate_to = escalate_to


def check_text(text: str, *, user=None, request=None) -> SafetyVerdict:
    """Screen a piece of text before it reaches a user."""
    if not text:
        return SafetyVerdict(allowed=True)

    for rule, pattern in BLOCK_PATTERNS.items():
        if pattern.search(text):
            _record(
                rule=rule,
                action=SafetyAction.BLOCKED,
                severity=SafetySeverity.HIGH,
                user=user,
                request=request,
            )
            return SafetyVerdict(
                allowed=False,
                action=SafetyAction.BLOCKED,
                rule=rule,
                severity=SafetySeverity.HIGH,
                message="ai.safety.blocked",
            )

    for rule, pattern in ESCALATION_PATTERNS.items():
        if pattern.search(text):
            _record(
                rule=rule,
                action=SafetyAction.ESCALATED,
                severity=SafetySeverity.MEDIUM,
                user=user,
                request=request,
            )
            return SafetyVerdict(
                allowed=False,
                action=SafetyAction.ESCALATED,
                rule=rule,
                message="ai.safety.escalate",
                escalate_to=rule,
            )

    return SafetyVerdict(allowed=True)


def _record(*, rule: str, action: str, severity: str, user=None, request=None) -> None:
    try:
        AISafetyEvent.objects.create(
            request=request,
            user=user if getattr(user, "pk", None) else None,
            rule=rule,
            severity=severity,
            action=action,
            details={},
        )
    except Exception:  # pragma: no cover - never break the caller
        pass
