"""AI endpoints."""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.common.exceptions import DomainError, NotAllowed
from apps.common.enums import Role
from apps.common.permissions import IsAdmin, IsStudent
from apps.taxonomy.models import Profession

from ..models import (
    AIFeedback,
    AIProviderConfig,
    AIRecommendation,
    AIRequestLog,
    AISafetyEvent,
)
from ..services import get_ai_service, get_recommendations, refresh_recommendations

logger = logging.getLogger(__name__)


class AIRecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIRecommendation
        fields = [
            "id",
            "type",
            "ref_type",
            "ref_id",
            "title",
            "score",
            "reason_code",
            "reason_text",
            "reason_data",
            "status",
            "created_at",
        ]
        read_only_fields = fields


class AIProviderConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIProviderConfig
        fields = [
            "id",
            "name",
            "provider",
            "model",
            "api_key_env_name",
            "endpoint",
            "params",
            "is_active",
            "max_tokens",
            "timeout_seconds",
            "monthly_budget",
        ]
        read_only_fields = ["id"]


class AIRequestLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIRequestLog
        fields = [
            "id",
            "use_case",
            "provider",
            "model",
            "prompt_version",
            "tokens_in",
            "tokens_out",
            "latency_ms",
            "status",
            "error_code",
            "created_at",
        ]
        read_only_fields = fields


@extend_schema(tags=["ai"])
class RecommendationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AIRecommendationSerializer
    filterset_fields = ["type", "status"]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai"

    def get_queryset(self):
        return AIRecommendation.objects.filter(user=self.request.user).order_by("-score")

    def list(self, request, *args, **kwargs):
        recommendations = get_recommendations(
            request.user,
            type=request.query_params.get("type"),
            limit=min(int(request.query_params.get("limit", 20)), 50),
        )
        return Response(AIRecommendationSerializer(recommendations, many=True).data)

    @extend_schema(request=None, responses={200: dict})
    @action(detail=False, methods=["post"])
    def refresh(self, request):
        return Response({"generated": refresh_recommendations(request.user)})

    @extend_schema(request=dict, responses={200: AIRecommendationSerializer})
    @action(detail=True, methods=["post"], url_path="feedback")
    def feedback(self, request, pk=None):
        """User verdict — the training signal for future ranking (TZ §13)."""
        recommendation = self.get_object()
        rating = request.data.get("rating")
        if rating not in {"UP", "DOWN"}:
            raise DomainError("Rating must be UP or DOWN.", code="invalid_rating")

        AIFeedback.objects.update_or_create(
            recommendation=recommendation,
            user=request.user,
            defaults={"rating": rating, "comment": request.data.get("comment", "")[:1000]},
        )
        recommendation.status = "ACCEPTED" if rating == "UP" else "DISMISSED"
        recommendation.save(update_fields=["status", "updated_at"])

        from apps.analytics.services import track

        track(
            request.user,
            "recommendation_accepted" if rating == "UP" else "recommendation_dismissed",
            {"type": recommendation.type, "ref_id": str(recommendation.ref_id)},
        )
        return Response(AIRecommendationSerializer(recommendation).data)


@extend_schema(tags=["ai"])
class AIAssistantView(APIView):
    """Analysis endpoints backing the student's AI assistant screen."""

    permission_classes = [IsAuthenticated, IsStudent]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai"

    def get(self, request):
        from apps.billing.enums import Feature as BillingFeature
        from apps.billing.services import consume

        # Premium capability, and every call costs a model round trip once a
        # real provider is wired — so it is metered, not merely permitted.
        consume(request.user, BillingFeature.AI_ASSISTANT)

        service = get_ai_service()
        knowledge = service.analyze_knowledge(request.user)

        profile = getattr(request.user, "student_profile", None)
        target_id = request.query_params.get("profession") or getattr(
            profile, "target_profession_id", None
        )
        gap = None
        if target_id:
            profession = Profession.objects.filter(id=target_id).first()
            if profession is not None:
                report = service.analyze_skills(request.user, profession)
                gap = {
                    "profession": report.profession,
                    "readiness": report.readiness,
                    "matching": report.matching,
                    "partial": report.partial,
                    "missing": report.missing,
                    "next_actions": report.next_actions,
                }

        return Response(
            {
                "knowledge": {
                    "average_score": knowledge.average_score,
                    "strongest": knowledge.strongest,
                    "weakest": knowledge.weakest,
                    "notes": knowledge.notes,
                },
                "skill_gap": gap,
                "careers": [
                    {
                        "type": r.type,
                        "ref_id": r.ref_id,
                        "title": r.title,
                        "score": r.score,
                        "reason_code": r.reason_code,
                        "reason_data": r.reason_data,
                    }
                    for r in service.generate_career_recommendations(request.user)
                ],
            }
        )


@extend_schema(tags=["ai"])
class CapabilityView(APIView):
    """Hard + soft skill analysis.

    Readable by the student about themselves, and by an employer about a
    candidate they can already reach — the same reachability rule the candidate
    card uses, so this endpoint cannot become a profile reader for any user id
    an employer types. Not metered: it reads data the platform already holds
    and the employer already paid to search.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai"

    def get(self, request):
        target = self._resolve_subject(request)
        insight = get_ai_service().analyze_capability(target)
        return Response(
            {
                "user_id": str(target.id),
                "hard": insight.hard,
                "soft": insight.soft,
                "balance": insight.balance,
                "notes": insight.notes,
                "next_actions": insight.next_actions,
            }
        )

    def _resolve_subject(self, request):
        user_id = request.query_params.get("user")
        if not user_id or str(user_id) == str(request.user.id):
            return request.user

        if request.user.role not in {Role.EMPLOYER, Role.ADMIN}:
            raise NotAllowed("You can only read your own analysis.")

        from apps.accounts.models import User
        from apps.matching.models import MatchResult

        subject = User.objects.filter(id=user_id).first()
        if subject is None:
            raise DomainError("Unknown user.", code="not_found")

        if request.user.role == Role.EMPLOYER:
            company = getattr(request.user, "employer_profile", None)
            reachable = MatchResult.objects.filter(
                student=subject, vacancy__employer=company
            ).exists()
            if not reachable:
                raise NotAllowed(
                    "This person is not a candidate for your vacancies.",
                    code="not_found",
                )
        return subject


@extend_schema(tags=["ai"])
class AIProviderConfigViewSet(viewsets.ModelViewSet):
    """Provider configuration (TZ §21 leaves the choice open).

    The API key never travels through here — only the name of the environment
    variable that holds it.
    """

    permission_classes = [IsAdmin]
    serializer_class = AIProviderConfigSerializer
    queryset = AIProviderConfig.objects.all().order_by("-is_active", "name")

    @extend_schema(request=None, responses={200: AIProviderConfigSerializer})
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        config = self.get_object()
        AIProviderConfig.objects.filter(is_active=True).exclude(pk=config.pk).update(
            is_active=False
        )
        config.is_active = True
        config.save(update_fields=["is_active", "updated_at"])

        from apps.audit.models import AuditAction, AuditSeverity
        from apps.audit.services import log_action

        log_action(
            action=AuditAction.CONFIG_CHANGE,
            obj=config,
            actor=request.user,
            note=f"AI provider switched to {config.provider}",
            severity=AuditSeverity.WARNING,
        )
        return Response(AIProviderConfigSerializer(config).data)


@extend_schema(tags=["ai"])
class AIMonitoringView(APIView):
    """AI monitoring panel for admins (prompt §21)."""

    permission_classes = [IsAdmin]

    def get(self, request):
        from django.db.models import Avg, Count
        from django.utils import timezone

        since = timezone.now() - timezone.timedelta(days=30)
        logs = AIRequestLog.objects.filter(created_at__gte=since)

        return Response(
            {
                "requests_30d": logs.count(),
                "by_use_case": list(
                    logs.values("use_case").annotate(count=Count("id")).order_by("-count")
                ),
                "by_status": list(
                    logs.values("status").annotate(count=Count("id")).order_by("-count")
                ),
                "avg_latency_ms": round(
                    logs.aggregate(v=Avg("latency_ms"))["v"] or 0
                ),
                "tokens": {
                    "in": sum(logs.values_list("tokens_in", flat=True)),
                    "out": sum(logs.values_list("tokens_out", flat=True)),
                },
                "safety_events": list(
                    AISafetyEvent.objects.filter(created_at__gte=since)
                    .values("rule", "action", "severity")
                    .annotate(count=Count("id"))
                    .order_by("-count")
                ),
                "feedback": list(
                    AIFeedback.objects.filter(created_at__gte=since)
                    .values("rating")
                    .annotate(count=Count("id"))
                ),
                "active_provider": AIProviderConfigSerializer(
                    AIProviderConfig.objects.filter(is_active=True).first()
                ).data
                if AIProviderConfig.objects.filter(is_active=True).exists()
                else {"provider": "RULE_BASED", "name": "default"},
            }
        )


@extend_schema(tags=["ai"])
class AIRequestLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdmin]
    serializer_class = AIRequestLogSerializer
    queryset = AIRequestLog.objects.all().order_by("-created_at")
    filterset_fields = ["use_case", "status", "provider"]


# ---------------------------------------------------------------------------
# Intake interview
# ---------------------------------------------------------------------------
class IntakeView(APIView):
    """The getting-to-know-you interview.

    GET returns the current question and progress; POST records one answer and
    returns the next. The client never decides what comes next — branching
    lives on the server so a refreshed tab or a second device resumes exactly
    where the person left off.
    """

    permission_classes = [IsAuthenticated, IsStudent]

    @extend_schema(responses={200: dict})
    def get(self, request):
        from ..intake_service import describe, get_or_start

        return Response(describe(get_or_start(request.user)))

    @extend_schema(request=dict, responses={200: dict})
    def post(self, request):
        from ..intake_service import complete, describe, get_or_start, skip, submit

        session = get_or_start(request.user)
        action_name = request.data.get("action", "answer")

        try:
            if action_name == "answer":
                question_id = request.data.get("question")
                if not question_id:
                    raise ValueError("unknown_question")
                submit(session, question_id, request.data.get("value"))
            elif action_name == "skip":
                skip(session, request.data.get("question"))
            elif action_name == "complete":
                complete(session)
            else:
                raise ValueError("unknown_action")
        except ValueError as error:
            raise DomainError(str(error), code=str(error))

        session.refresh_from_db()
        return Response(describe(session))


# ---------------------------------------------------------------------------
# Grounded chat
# ---------------------------------------------------------------------------
class ChatView(APIView):
    """Ask the assistant about your own numbers.

    Available to learners and employers. Every reply is built from rows the
    caller can already see — the chat is a way of reading them out loud, not a
    second opinion about them.

    Messages belong to a thread so past conversations stay findable. Without a
    `thread` parameter the most recent one is continued, which is what makes
    the floating panel and the full view show the same conversation.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai"

    def _assert_role(self, user):
        if user.role not in {Role.STUDENT, Role.EMPLOYER}:
            raise NotAllowed("The assistant is available to learners and employers.")

    def _resolve_thread(self, user, thread_id: str | None, *, create: bool):
        from ..models import ChatThread

        if thread_id:
            thread = ChatThread.objects.filter(id=thread_id, user=user).first()
            if thread is None:
                raise DomainError("Unknown conversation.", code="unknown_thread")
            return thread

        thread = ChatThread.objects.filter(user=user).first()  # ordering: newest
        if thread is None and create:
            thread = ChatThread.objects.create(user=user)
        return thread

    @extend_schema(responses={200: dict})
    def get(self, request):
        from ..models import ChatMessage, ChatThread

        self._assert_role(request.user)

        threads = ChatThread.objects.filter(user=request.user)[:50]
        thread = self._resolve_thread(
            request.user, request.query_params.get("thread"), create=False
        )

        messages = []
        if thread is not None:
            rows = ChatMessage.objects.filter(thread=thread).order_by("created_at")[:200]
            messages = [_chat_row(row) for row in rows]

        return Response(
            {
                "thread": str(thread.id) if thread else None,
                "messages": messages,
                "threads": [_thread_row(item) for item in threads],
            }
        )

    @extend_schema(request=dict, responses={200: dict})
    def post(self, request):
        from apps.billing.enums import Feature as BillingFeature
        from apps.billing.services import consume
        from django.utils import timezone

        from ..chat import answer as build_answer
        from ..models import ChatAuthor, ChatMessage, ChatThread
        from ..safety import check_text
        from ..services import get_ai_service

        self._assert_role(request.user)

        # "new" starts a fresh conversation without sending anything.
        if request.data.get("action") == "new":
            thread = ChatThread.objects.create(user=request.user)
            return Response({"thread": str(thread.id), "messages": [], "threads": [
                _thread_row(item) for item in ChatThread.objects.filter(user=request.user)[:50]
            ]})

        text = str(request.data.get("text", "")).strip()
        if not text:
            raise DomainError("Message is empty.", code="empty_message")
        if len(text) > 2000:
            raise DomainError("Message is too long.", code="message_too_long")

        consume(request.user, BillingFeature.AI_CHAT)

        thread = self._resolve_thread(
            request.user, request.data.get("thread"), create=True
        )

        # Screened before anything is looked up, so a refused message never
        # touches the data layer.
        verdict = check_text(text, user=request.user, request=request)
        provider = get_ai_service().provider

        ChatMessage.objects.create(
            user=request.user,
            thread=thread,
            author=ChatAuthor.USER,
            text=text,
            blocked=not verdict.allowed,
            block_rule=verdict.rule,
            provider=provider,
        )

        # The first question names the conversation: it is the line the person
        # will scan for later, and it costs no model call.
        if not thread.title:
            thread.title = text[:120]

        if verdict.allowed:
            # The facts are read first and always. A hosted model, if one is
            # configured, is then asked to phrase *these* facts — it is never
            # the thing that decides what is true.
            result = build_answer(request.user, text)
            free_text = _phrase_with_model(request.user, thread, text, result.facts)

            reply = ChatMessage.objects.create(
                user=request.user,
                thread=thread,
                author=ChatAuthor.ASSISTANT,
                text=free_text,
                code=result.code,
                intent=result.intent,
                grounding=result.facts,
                sources=result.sources,
                suggestions=result.suggestions,
                provider=provider,
            )
        else:
            reply = ChatMessage.objects.create(
                user=request.user,
                thread=thread,
                author=ChatAuthor.ASSISTANT,
                code="safety.blocked",
                intent="BLOCKED",
                grounding={"rule": verdict.rule, "escalate_to": verdict.escalate_to},
                blocked=True,
                block_rule=verdict.rule,
                provider=provider,
            )

        thread.last_message_at = timezone.now()
        thread.save(update_fields=["title", "last_message_at", "updated_at"])

        return Response({"thread": str(thread.id), "message": _chat_row(reply)})

    @extend_schema(responses={204: None})
    def delete(self, request):
        """Delete one conversation, or every conversation when none is named."""
        from ..models import ChatThread

        self._assert_role(request.user)

        thread_id = request.query_params.get("thread")
        if thread_id:
            ChatThread.objects.filter(id=thread_id, user=request.user).delete()
        else:
            ChatThread.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _thread_row(thread) -> dict:
    return {
        "id": str(thread.id),
        "title": thread.title,
        "last_message_at": (
            thread.last_message_at.isoformat() if thread.last_message_at else None
        ),
        "created_at": thread.created_at.isoformat(),
    }


def _phrase_with_model(user, thread, question: str, facts: dict) -> str:
    """Ask the configured model to phrase the facts. Empty string if none is.

    Every failure path returns "" rather than raising: the rule-based sentence
    is already prepared and correct, so a model outage should cost fluency, not
    the answer.
    """
    from ..backends import get_chat_backend
    from ..models import ChatAuthor, ChatMessage, preferences_for
    from ..safety import check_text

    backend = get_chat_backend()
    if backend is None:
        return ""

    history = [
        {
            "role": "user" if m.author == ChatAuthor.USER else "assistant",
            "content": m.text,
        }
        for m in ChatMessage.objects.filter(thread=thread).order_by("created_at")[:20]
        if m.text
    ]

    try:
        # The person, not just their question. The system prompt is composed
        # from their role, what their account actually contains, and anything
        # they set in their own assistant preferences — see apps/ai/persona.py.
        text = backend.reply(
            question=question,
            facts=facts,
            history=history,
            user=user,
            preferences=preferences_for(user),
        )
    except Exception:
        logger.exception("Chat backend failed; falling back to the rule-based answer.")
        return ""

    # The model's output is screened like any other text before it is shown.
    if not check_text(text, user=user).allowed:
        return ""
    return text[:4000]


def _chat_row(message) -> dict:
    return {
        "id": str(message.id),
        "thread": str(message.thread_id) if message.thread_id else None,
        "author": message.author,
        "text": message.text,
        "code": message.code,
        "intent": message.intent,
        "grounding": message.grounding,
        "sources": message.sources,
        "suggestions": message.suggestions,
        "blocked": message.blocked,
        "created_at": message.created_at.isoformat(),
    }


class SafetyEventSerializer(serializers.ModelSerializer):
    """One intervention, with enough about the person to act on it.

    The name is here on purpose. An admin looking at a self-harm escalation
    cannot follow it up against a uuid, and following it up is the entire
    reason the layer records anything. Reading the queue is logged as a
    personal-data access for the same reason.
    """

    user_email = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()
    user_role = serializers.SerializerMethodField()
    is_minor = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AISafetyEvent
        fields = [
            "id",
            "rule",
            "severity",
            "action",
            "details",
            "user",
            "user_email",
            "user_name",
            "user_role",
            "is_minor",
            "reviewed_at",
            "reviewed_by",
            "reviewed_by_name",
            "review_note",
            "created_at",
        ]
        read_only_fields = fields

    def get_user_email(self, event) -> str | None:
        return event.user.email if event.user_id else None

    def get_user_name(self, event) -> str | None:
        profile = getattr(event.user, "student_profile", None) if event.user_id else None
        return getattr(profile, "full_name", None)

    def get_user_role(self, event) -> str | None:
        return event.user.role if event.user_id else None

    def get_is_minor(self, event) -> bool:
        """Surfaced because it changes what the right response is."""
        profile = getattr(event.user, "student_profile", None) if event.user_id else None
        return bool(getattr(profile, "is_minor", False))

    def get_reviewed_by_name(self, event) -> str | None:
        return event.reviewed_by.display_name if event.reviewed_by_id else None


@extend_schema(tags=["ai"])
class SafetyEventViewSet(viewsets.ReadOnlyModelViewSet):
    """The safety queue: what the filter stopped, and who it happened to.

    The monitoring panel already counted these — "self_harm: 3" — which is
    exactly as much use as it sounds. A count cannot be followed up: it does
    not say who, or when, or whether anybody has looked. On a platform serving
    minors that is the one queue somebody has to be able to work through, so
    it is a list with names, an open/closed state, and a note.
    """

    permission_classes = [IsAdmin]
    serializer_class = SafetyEventSerializer
    filterset_fields = ["rule", "action", "severity"]

    def get_queryset(self):
        queryset = AISafetyEvent.objects.select_related(
            "user", "user__student_profile", "reviewed_by"
        )
        state = self.request.query_params.get("state")
        if state == "open":
            queryset = queryset.filter(reviewed_at__isnull=True)
        elif state == "closed":
            queryset = queryset.filter(reviewed_at__isnull=False)
        return queryset

    def list(self, request, *args, **kwargs):
        # Reading this is reading who said what, so it is recorded like any
        # other access to personal data.
        from apps.audit.models import AuditAction, AuditSeverity
        from apps.audit.services import log_action

        log_action(
            action=AuditAction.PII_ACCESS,
            obj=None,
            actor=request.user,
            note="viewed the AI safety queue",
            severity=AuditSeverity.NOTICE,
        )
        return super().list(request, *args, **kwargs)

    @extend_schema(request=dict, responses={200: SafetyEventSerializer})
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        """Close an event, with a note on what was done about it."""
        from django.utils import timezone

        event = self.get_object()
        event.reviewed_at = timezone.now()
        event.reviewed_by = request.user
        event.review_note = str(request.data.get("note", ""))[:2000]
        event.save(update_fields=["reviewed_at", "reviewed_by", "review_note", "updated_at"])
        return Response(SafetyEventSerializer(event).data)

    @extend_schema(request=None, responses={200: SafetyEventSerializer})
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        """Put it back in the queue — closing one by mistake must be undoable."""
        event = self.get_object()
        event.reviewed_at = None
        event.reviewed_by = None
        event.save(update_fields=["reviewed_at", "reviewed_by", "updated_at"])
        return Response(SafetyEventSerializer(event).data)

    @extend_schema(responses={200: dict})
    @action(detail=False, methods=["get"])
    def summary(self, request):
        """How much is open, and how bad — for the dashboard badge."""
        from django.db.models import Count

        open_events = AISafetyEvent.objects.filter(reviewed_at__isnull=True)
        return Response(
            {
                "open": open_events.count(),
                "open_high": open_events.filter(severity="HIGH").count(),
                "by_rule": list(
                    open_events.values("rule", "action")
                    .annotate(count=Count("id"))
                    .order_by("-count")
                ),
            }
        )


class AssistantPreferencesView(APIView):
    """What this person wants the assistant to sound like.

    GET returns both halves and keeps them apart: `chosen` is what they set,
    `effective` is what the assistant will actually use. They differ wherever
    nothing was chosen, because the unset fields are filled from the account's
    own stage -- and showing only the effective values would make a derived
    default look like a decision the person made.

    `stage` is returned too. It is the assistant's read of where they are, and
    it is the reason the defaults are what they are; a settings screen that
    changes behaviour without saying why is a settings screen people distrust.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: dict})
    def get(self, request):
        from ..models import AssistantProfile
        from ..persona import default_preferences, stage_for

        stage = stage_for(request.user)
        profile = AssistantProfile.objects.filter(user=request.user).first()
        chosen = profile.overrides() if profile else {}

        return Response(
            {
                "stage": stage,
                "chosen": {
                    "detail": (profile.detail if profile else "") or None,
                    "tone": (profile.tone if profile else "") or None,
                    "explain_terms": profile.explain_terms if profile else None,
                },
                "effective": {**default_preferences(stage), **chosen},
            }
        )

    @extend_schema(request=dict, responses={200: dict})
    def patch(self, request):
        """Set or clear a preference.

        null clears a field back to "follow the stage" -- deliberately
        reachable, because somebody who tried "short answers" and did not like
        it should be able to get back to the adaptive default rather than
        having to guess which of the three fixed options it was.
        """
        from ..models import AnswerDetail, AnswerTone, AssistantProfile

        profile, _ = AssistantProfile.objects.get_or_create(user=request.user)

        if "detail" in request.data:
            value = request.data["detail"]
            if value not in (None, "", *AnswerDetail.values):
                raise DomainError("Unknown detail level.", code="validation_error")
            profile.detail = value or ""

        if "tone" in request.data:
            value = request.data["tone"]
            if value not in (None, "", *AnswerTone.values):
                raise DomainError("Unknown tone.", code="validation_error")
            profile.tone = value or ""

        if "explain_terms" in request.data:
            value = request.data["explain_terms"]
            if value is not None and not isinstance(value, bool):
                raise DomainError("explain_terms is true, false or null.",
                                  code="validation_error")
            profile.explain_terms = value

        profile.save()
        return self.get(request)
