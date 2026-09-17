"""Assessment serializers.

The split between `*ForTaking` and `*WithAnswers` is load-bearing: the taking
serializers must never expose `is_correct` or `accepted_answers`, or the test is
solvable from the network tab.
"""

from django.conf import settings
from rest_framework import serializers

from apps.common.serializers import TranslatedField

from ..models import (
    AnswerOption,
    Question,
    Test,
    TestAttempt,
    TestSkill,
    TestSkillResult,
)


class TestSkillSerializer(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")

    class Meta:
        model = TestSkill
        fields = ["id", "skill", "skill_name", "weight"]


class AnswerOptionForTakingSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnswerOption
        fields = ["id", "text", "order"]  # `is_correct` deliberately absent


class QuestionForTakingSerializer(serializers.ModelSerializer):
    options = AnswerOptionForTakingSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = ["id", "text", "type", "points", "order", "options"]


class AnswerOptionAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnswerOption
        # `weight` is authoring data for SITUATIONAL options and stays on this
        # side of the split: a student who can read the weights can pick the
        # highest one without answering the question.
        fields = ["id", "text", "is_correct", "weight", "order"]


class QuestionAdminSerializer(serializers.ModelSerializer):
    options = AnswerOptionAdminSerializer(many=True, required=False)

    class Meta:
        model = Question
        fields = [
            "id",
            "text",
            "type",
            "points",
            "order",
            "explanation",
            "skill",
            "accepted_answers",
            "options",
        ]

    def create(self, validated_data):
        options = validated_data.pop("options", [])
        question = Question.objects.create(**validated_data)
        AnswerOption.objects.bulk_create(
            [AnswerOption(question=question, **option) for option in options]
        )
        return question

    def update(self, instance, validated_data):
        options = validated_data.pop("options", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if options is not None:
            instance.options.all().delete()
            AnswerOption.objects.bulk_create(
                [AnswerOption(question=instance, **option) for option in options]
            )
        return instance


class TestListSerializer(serializers.ModelSerializer):
    skills = TestSkillSerializer(source="skill_links", many=True, read_only=True)
    question_count = serializers.SerializerMethodField()
    my_attempts = serializers.SerializerMethodField()
    provider_name = serializers.SerializerMethodField()
    #: Whether the platform owns this test, rather than an employer.
    #:
    #: The client used to work this out by comparing provider_name against the
    #: literal "Yoshlar Kapitali". That is an ownership question answered with
    #: a display name: it breaks the moment the product is renamed — which is
    #: exactly what happened — or an employer registers under the same name.
    is_platform = serializers.SerializerMethodField()

    class Meta:
        model = Test
        fields = [
            "id",
            "title",
            "description",
            "language",
            "type",
            "course",
            "passing_score",
            "time_limit_minutes",
            "max_attempts",
            "status",
            "is_public",
            "published_at",
            "skills",
            "question_count",
            "my_attempts",
            "provider_name",
            "is_platform",
        ]

    def get_question_count(self, test) -> int:
        # Annotated by the list queryset; the fallback keeps a single object
        # fetched some other way from returning nothing.
        annotated = getattr(test, "questions_total", None)
        return annotated if annotated is not None else test.questions.count()

    def get_provider_name(self, test) -> str:
        return (
            test.employer.display_name
            if test.employer_id
            else settings.PLATFORM_NAME
        )

    def get_is_platform(self, test) -> bool:
        return test.employer_id is None

    def get_my_attempts(self, test) -> dict | None:
        cache = self.context.get("attempts_by_test")
        if cache is None:
            return None
        attempts = cache.get(test.id, [])
        return {
            "used": len(attempts),
            "remaining": max(test.max_attempts - len(attempts), 0),
            "best_percentage": max((a.percentage for a in attempts), default=0),
            "passed": any(a.passed for a in attempts),
        }


class TestWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Test
        fields = [
            "id",
            "title",
            "description",
            "language",
            "type",
            "course",
            "passing_score",
            "time_limit_minutes",
            "max_attempts",
            "shuffle_questions",
            "show_correct_answers",
            "is_public",
        ]


class TestSkillResultSerializer(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")

    class Meta:
        model = TestSkillResult
        fields = [
            "skill",
            "skill_name",
            "percentage",
            "questions_total",
            "questions_correct",
        ]
        read_only_fields = fields


class AttemptSerializer(serializers.ModelSerializer):
    test_title = serializers.CharField(source="test.title", read_only=True)
    test_type = serializers.CharField(source="test.type", read_only=True)
    skill_results = TestSkillResultSerializer(many=True, read_only=True)

    class Meta:
        model = TestAttempt
        fields = [
            "id",
            "test",
            "test_title",
            "test_type",
            "attempt_no",
            "started_at",
            "expires_at",
            "submitted_at",
            "status",
            "score",
            "max_score",
            "percentage",
            "passed",
            "time_spent_seconds",
            "skill_results",
        ]
        read_only_fields = fields


class AttemptWithQuestionsSerializer(AttemptSerializer):
    questions = serializers.SerializerMethodField()

    class Meta(AttemptSerializer.Meta):
        fields = [*AttemptSerializer.Meta.fields, "questions"]

    def get_questions(self, attempt) -> list[dict]:
        questions = attempt.test.questions.prefetch_related("options").all()
        if attempt.test.shuffle_questions:
            questions = sorted(questions, key=lambda q: str(q.id))
        return QuestionForTakingSerializer(questions, many=True).data


class SubmitAnswerSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    option_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True
    )
    text = serializers.CharField(required=False, allow_blank=True, max_length=500)


class SubmitAttemptSerializer(serializers.Serializer):
    answers = SubmitAnswerSerializer(many=True)
