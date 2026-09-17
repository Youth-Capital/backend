"""AI configuration, recommendations, logging and safety (TZ §13, prompt §13/§19/§21)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel


class AIProvider(models.TextChoices):
    RULE_BASED = "RULE_BASED", _("Rule based (no external calls)")
    ANTHROPIC = "ANTHROPIC", _("Anthropic")
    OPENAI = "OPENAI", _("OpenAI")
    CUSTOM = "CUSTOM", _("Custom endpoint")


class AIProviderConfig(BaseModel):
    """Which engine answers AI requests.

    The API key itself is never stored here — only the *name* of the
    environment variable holding it. A database dump must not be a credential
    leak, and TZ §21 leaves the provider choice open.
    """

    name = models.CharField(max_length=64, unique=True)
    provider = models.CharField(
        max_length=16, choices=AIProvider.choices, default=AIProvider.RULE_BASED
    )
    model = models.CharField(max_length=120, blank=True)
    api_key_env_name = models.CharField(
        max_length=64,
        blank=True,
        help_text="Name of the environment variable holding the key, e.g. ANTHROPIC_API_KEY.",
    )
    endpoint = models.URLField(blank=True)
    params = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=False)
    max_tokens = models.PositiveIntegerField(default=2048)
    timeout_seconds = models.PositiveSmallIntegerField(default=30)
    monthly_budget = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "ai_provider_config"
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="only_one_active_ai_provider",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.provider})"


class AIUseCase(models.TextChoices):
    CAREER = "CAREER", _("Career guidance")
    LEARNING = "LEARNING", _("Learning recommendations")
    SKILL_GAP = "SKILL_GAP", _("Skill gap analysis")
    PLAN = "PLAN", _("Development plan generation")
    CV = "CV", _("CV assistance")
    MATCH_EXPLAIN = "MATCH_EXPLAIN", _("Match explanation")
    COACH = "COACH", _("Coaching conversation")


class AIRequestStatus(models.TextChoices):
    SUCCESS = "SUCCESS", _("Success")
    BLOCKED = "BLOCKED", _("Blocked by safety filter")
    ERROR = "ERROR", _("Error")
    TIMEOUT = "TIMEOUT", _("Timeout")


class AIRequestLog(BaseModel):
    """Evaluation log required by TZ §13.

    Stores a hash of the input, not the input: the prompts contain a young
    person's profile, and an operational log is the wrong place for it. Set
    AI_LOG_PROMPT_CONTENT=true only in a controlled debugging environment.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_requests",
    )
    use_case = models.CharField(max_length=16, choices=AIUseCase.choices)
    provider = models.CharField(max_length=16, choices=AIProvider.choices)
    model = models.CharField(max_length=120, blank=True)
    prompt_version = models.CharField(max_length=32, default="v1")

    input_hash = models.CharField(max_length=64, blank=True)
    input_preview = models.TextField(blank=True)

    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=8, choices=AIRequestStatus.choices, default=AIRequestStatus.SUCCESS
    )
    error_code = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "ai_request_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["use_case", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]


class RecommendationType(models.TextChoices):
    COURSE = "COURSE", _("Course")
    VACANCY = "VACANCY", _("Vacancy")
    SKILL = "SKILL", _("Skill")
    TASK = "TASK", _("Task")
    PROFESSION = "PROFESSION", _("Profession")
    TEST = "TEST", _("Test")


class RecommendationStatus(models.TextChoices):
    NEW = "NEW", _("New")
    SEEN = "SEEN", _("Seen")
    ACCEPTED = "ACCEPTED", _("Accepted")
    DISMISSED = "DISMISSED", _("Dismissed")


class AIRecommendation(BaseModel):
    """A single suggestion with its reason.

    `reason_data` carries the facts behind the suggestion so the UI can always
    answer "why this course / vacancy?" — TZ §13 requires the explanation, and
    an unexplained recommendation is one users learn to ignore.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recommendations"
    )
    type = models.CharField(max_length=12, choices=RecommendationType.choices)
    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.UUIDField(null=True, blank=True)

    title = models.CharField(max_length=255, blank=True)
    score = models.PositiveSmallIntegerField(default=0)
    reason_code = models.CharField(max_length=64, blank=True)
    reason_text = models.TextField(blank=True)
    reason_data = models.JSONField(default=dict, blank=True)

    use_case = models.CharField(
        max_length=16, choices=AIUseCase.choices, default=AIUseCase.LEARNING
    )
    request = models.ForeignKey(
        AIRequestLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recommendations",
    )
    status = models.CharField(
        max_length=10,
        choices=RecommendationStatus.choices,
        default=RecommendationStatus.NEW,
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_recommendation"
        ordering = ["-score", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "type", "ref_id"],
                name="uniq_recommendation_per_target",
            )
        ]
        indexes = [models.Index(fields=["user", "type", "status"])]

    def __str__(self) -> str:
        return f"{self.type} · {self.title} ({self.score})"


class FeedbackRating(models.TextChoices):
    UP = "UP", _("Helpful")
    DOWN = "DOWN", _("Not helpful")


class AIFeedback(BaseModel):
    """User verdict on a recommendation — the ground truth for future ranking."""

    recommendation = models.ForeignKey(
        AIRecommendation, on_delete=models.CASCADE, related_name="feedback"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_feedback"
    )
    rating = models.CharField(max_length=4, choices=FeedbackRating.choices)
    comment = models.TextField(blank=True, max_length=1000)

    class Meta:
        db_table = "ai_feedback"
        constraints = [
            models.UniqueConstraint(
                fields=["recommendation", "user"], name="uniq_ai_feedback"
            )
        ]


class SafetyAction(models.TextChoices):
    BLOCKED = "BLOCKED", _("Blocked")
    REDACTED = "REDACTED", _("Redacted")
    ESCALATED = "ESCALATED", _("Escalated to a human specialist")


class SafetySeverity(models.TextChoices):
    LOW = "LOW", _("Low")
    MEDIUM = "MEDIUM", _("Medium")
    HIGH = "HIGH", _("High")


class AISafetyEvent(BaseModel):
    """Record of the safety layer intervening (TZ §13).

    The platform serves minors, so refusals to give medical or legal verdicts
    and blocks on harmful content are auditable events, not silent behaviour.
    """

    request = models.ForeignKey(
        AIRequestLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="safety_events",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_safety_events",
    )
    rule = models.CharField(max_length=64)
    severity = models.CharField(
        max_length=8, choices=SafetySeverity.choices, default=SafetySeverity.MEDIUM
    )
    action = models.CharField(max_length=10, choices=SafetyAction.choices)
    details = models.JSONField(default=dict, blank=True)

    #: Whether a human has looked at this and closed it.
    #
    # Without these the list only grows: an escalation for self-harm sits at
    # the top of the page forever, and nobody can tell the ones somebody acted
    # on from the ones nobody has read. A safety queue that cannot be worked
    # through stops being read at all.
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_safety_events",
    )
    review_note = models.TextField(blank=True, max_length=2000)

    class Meta:
        db_table = "ai_safety_event"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["rule", "-created_at"]),
            # The queue is read as "what is still open, worst first".
            models.Index(fields=["reviewed_at", "severity", "-created_at"]),
        ]


class IntakeStatus(models.TextChoices):
    IN_PROGRESS = "IN_PROGRESS", _("In progress")
    COMPLETED = "COMPLETED", _("Completed")
    ABANDONED = "ABANDONED", _("Abandoned")


class IntakeSession(BaseModel):
    """The getting-to-know-you interview a learner answers before the dashboard.

    Answers are kept as given, separately from what was written to the profile.
    Two reasons: the person can be shown what they said and correct it, and if
    the extraction rules change later the interview can be re-applied without
    asking anyone anything twice.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="intake_sessions",
    )
    status = models.CharField(
        max_length=16,
        choices=IntakeStatus.choices,
        default=IntakeStatus.IN_PROGRESS,
        db_index=True,
    )

    #: {question_id: value} exactly as answered.
    answers = models.JSONField(default=dict, blank=True)
    #: What was derived and written to the profile.
    applied = models.JSONField(default=dict, blank=True)

    provider = models.CharField(max_length=32, default=AIProvider.RULE_BASED)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_intake_session"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user",),
                condition=models.Q(status="IN_PROGRESS"),
                name="ai_one_active_intake_per_user",
            ),
        ]
        indexes = [models.Index(fields=("user", "status"))]

    def __str__(self) -> str:
        return f"intake:{self.user_id} [{self.status}]"


class ChatThread(BaseModel):
    """One conversation.

    Threads exist so a question asked last week is still findable next to the
    answer it got, instead of scrolling through one endless log. The title is
    taken from the first question the person typed — not generated — because a
    line they wrote themselves is the thing they will recognise later.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_threads",
    )
    title = models.CharField(max_length=120, blank=True)
    #: Sorting key. Set on creation rather than left null, because a brand new
    #: conversation *is* the most recent one — leaving it null sorted it last,
    #: so the next message without an explicit thread landed in the previous
    #: conversation instead of the one just started.
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "ai_chat_thread"
        ordering = ("-last_message_at", "-created_at")
        indexes = [models.Index(fields=("user", "-last_message_at"))]

    def __str__(self) -> str:
        return self.title or f"thread:{self.id}"


class ChatAuthor(models.TextChoices):
    USER = "USER", _("User")
    ASSISTANT = "ASSISTANT", _("Assistant")


class ChatMessage(BaseModel):
    """One turn of the grounded chat.

    `grounding` holds the facts the answer was built from — the same rows the
    user could open themselves. Storing them makes an answer auditable after
    the fact: if someone disputes a number, the reply and the data it came
    from are both still here, rather than only the sentence.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )
    #: Nullable only so existing rows survive the migration; every new message
    #: is written with one.
    thread = models.ForeignKey(
        ChatThread,
        on_delete=models.CASCADE,
        related_name="messages",
        null=True,
        blank=True,
    )
    author = models.CharField(max_length=10, choices=ChatAuthor.choices)
    text = models.TextField(max_length=4000, blank=True)

    #: Assistant turns only: the answer template and what filled it.
    code = models.CharField(max_length=48, blank=True)
    intent = models.CharField(max_length=32, blank=True)
    grounding = models.JSONField(default=dict, blank=True)
    sources = models.JSONField(default=list, blank=True)
    suggestions = models.JSONField(default=list, blank=True)

    #: A turn the safety filter refused. Kept, not deleted — the record of a
    #: refusal is what makes escalation reviewable.
    blocked = models.BooleanField(default=False)
    block_rule = models.CharField(max_length=64, blank=True)

    provider = models.CharField(max_length=32, default=AIProvider.RULE_BASED)

    class Meta:
        db_table = "ai_chat_message"
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=("user", "created_at")),
            models.Index(fields=("thread", "created_at")),
        ]

    def __str__(self) -> str:
        return f"{self.author}:{self.code or self.text[:40]}"


class AnswerDetail(models.TextChoices):
    BRIEF = "BRIEF", _("Short")
    NORMAL = "NORMAL", _("Normal")
    DETAILED = "DETAILED", _("Detailed")


class AnswerTone(models.TextChoices):
    WARM = "WARM", _("Encouraging")
    NEUTRAL = "NEUTRAL", _("Neutral")
    DIRECT = "DIRECT", _("Direct")


class AssistantProfile(BaseModel):
    """How one person wants the assistant to talk to them.

    Every field is nullable, and that is the design rather than laziness. The
    assistant already adapts on its own -- see apps/ai/persona.py, which reads
    the account's actual stage and picks a register from it -- so a row here is
    a *correction*, not a configuration. Null means "whatever you worked out",
    and someone who never opens the setting gets an answer pitched at where
    they are rather than at a default somebody picked once for everybody.

    That distinction matters for the shape of the thing: a NOT NULL column with
    a default would silently freeze a new learner's preferences at the moment
    they signed up, and they would still be getting beginner explanations a
    year later.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assistant_profile",
    )

    #: Null: follow the stage. Set: this person asked for this length.
    detail = models.CharField(
        max_length=8, choices=AnswerDetail.choices, blank=True, default=""
    )
    tone = models.CharField(
        max_length=8, choices=AnswerTone.choices, blank=True, default=""
    )
    #: Null (None) rather than False: "I did not choose" is not "no".
    explain_terms = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = "ai_assistant_profile"

    def __str__(self) -> str:
        return f"assistant preferences for {self.user_id}"

    def overrides(self) -> dict:
        """Only the fields this person actually chose.

        Merged over the derived defaults by persona.build_system_prompt, so an
        unset field is absent from the dict rather than present-and-empty --
        an empty string would override the derived value with nothing.
        """
        chosen = {}
        if self.detail:
            chosen["detail"] = self.detail
        if self.tone:
            chosen["tone"] = self.tone
        if self.explain_terms is not None:
            chosen["explain_terms"] = self.explain_terms
        return chosen


def preferences_for(user) -> dict:
    """This person's chosen overrides, or {} when they have none."""
    profile = AssistantProfile.objects.filter(user=user).first()
    return profile.overrides() if profile else {}
