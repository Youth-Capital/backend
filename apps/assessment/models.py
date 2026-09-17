"""Tests, questions and attempts (prompt §8)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.enums import Language, ModerationStatus
from apps.common.models import BaseModel
from apps.learning.models import Course
from apps.profiles.models import EmployerProfile
from apps.taxonomy.models import Skill


class TestType(models.TextChoices):
    COURSE_TEST = "COURSE_TEST", _("Course test")
    SKILL_TEST = "SKILL_TEST", _("Standalone skill test")
    DIAGNOSTIC = "DIAGNOSTIC", _("Onboarding diagnostic")
    #: Employer-owned screening. Deliberately excluded from the global
    #: knowledge profile — see docs/01-ANALYSIS.md §3.2.
    SCREENING = "SCREENING", _("Employer screening")
    #: Situational judgement over behavioural competencies. Scored by option
    #: weight rather than by right and wrong, and it never fails: a
    #: soft-skill profile is a shape, not a pass mark.
    SOFT_SKILL = "SOFT_SKILL", _("Soft skills assessment")


class Test(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    language = models.CharField(
        max_length=2, choices=Language.choices, default=Language.UZ
    )
    type = models.CharField(
        max_length=12, choices=TestType.choices, default=TestType.SKILL_TEST
    )

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, null=True, blank=True, related_name="tests"
    )
    employer = models.ForeignKey(
        EmployerProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tests",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="authored_tests"
    )

    passing_score = models.PositiveSmallIntegerField(default=60)
    time_limit_minutes = models.PositiveSmallIntegerField(default=30)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    shuffle_questions = models.BooleanField(default=True)
    show_correct_answers = models.BooleanField(default=True)

    status = models.CharField(
        max_length=16, choices=ModerationStatus.choices, default=ModerationStatus.DRAFT
    )
    moderation_note = models.TextField(blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderated_tests",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    is_public = models.BooleanField(default=True)

    class Meta:
        db_table = "assessment_test"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "type"]),
            models.Index(fields=["employer", "status"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def feeds_knowledge_profile(self) -> bool:
        """Only moderated, non-screening tests may move a global score."""
        return (
            self.status == ModerationStatus.PUBLISHED and self.type != TestType.SCREENING
        )

    @property
    def is_soft_skill(self) -> bool:
        return self.type == TestType.SOFT_SKILL

    @property
    def max_score(self) -> int:
        return sum(q.points for q in self.questions.all())


class TestSkill(BaseModel):
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="skill_links")
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="test_links")
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=1.0)

    class Meta:
        db_table = "assessment_test_skill"
        constraints = [
            models.UniqueConstraint(fields=["test", "skill"], name="uniq_test_skill")
        ]


class QuestionType(models.TextChoices):
    SINGLE = "SINGLE", _("Single choice")
    MULTIPLE = "MULTIPLE", _("Multiple choice")
    TRUE_FALSE = "TRUE_FALSE", _("True / false")
    SHORT_ANSWER = "SHORT_ANSWER", _("Short answer")
    #: "What would you do?" — every option is a defensible action, and each
    #: carries a weight saying how much of the competency it demonstrates.
    #: There is no correct answer to leak, which is why these questions can
    #: show their explanations without giving the test away.
    SITUATIONAL = "SITUATIONAL", _("Situational judgement")


class Question(BaseModel):
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    type = models.CharField(
        max_length=14, choices=QuestionType.choices, default=QuestionType.SINGLE
    )
    points = models.PositiveSmallIntegerField(default=1)
    order = models.PositiveSmallIntegerField(default=0)
    explanation = models.TextField(blank=True)
    skill = models.ForeignKey(
        Skill,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions",
    )
    #: Accepted strings for SHORT_ANSWER, compared case-insensitively.
    accepted_answers = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "assessment_question"
        ordering = ["order", "created_at"]

    def __str__(self) -> str:
        return self.text[:80]


class AnswerOption(BaseModel):
    """`is_correct` must never be serialised to a student before submission."""

    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="options"
    )
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    #: Percentage of the question's points this option is worth. Used by
    #: SITUATIONAL questions, where "worse" and "wrong" are different things:
    #: escalating immediately is not wrong, it just shows less independence
    #: than trying first. Ignored by the right/wrong question types, which
    #: score from `is_correct`.
    weight = models.PositiveSmallIntegerField(default=0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "assessment_answer_option"
        ordering = ["order", "created_at"]


class AttemptStatus(models.TextChoices):
    IN_PROGRESS = "IN_PROGRESS", _("In progress")
    SUBMITTED = "SUBMITTED", _("Submitted")
    EXPIRED = "EXPIRED", _("Expired")
    GRADED = "GRADED", _("Graded")


class TestAttempt(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="test_attempts"
    )
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="attempts")
    attempt_no = models.PositiveSmallIntegerField(default=1)

    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=AttemptStatus.choices, default=AttemptStatus.IN_PROGRESS
    )

    score = models.PositiveSmallIntegerField(default=0)
    max_score = models.PositiveSmallIntegerField(default=0)
    percentage = models.PositiveSmallIntegerField(default=0)
    passed = models.BooleanField(default=False)
    time_spent_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "assessment_test_attempt"
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "test", "attempt_no"], name="uniq_test_attempt"
            )
        ]
        indexes = [
            models.Index(fields=["test", "passed"]),
            models.Index(fields=["user", "test"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} · {self.test.title} #{self.attempt_no}: {self.percentage}%"


class AttemptAnswer(BaseModel):
    attempt = models.ForeignKey(
        TestAttempt, on_delete=models.CASCADE, related_name="answers"
    )
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="attempt_answers"
    )
    selected_options = models.ManyToManyField(AnswerOption, blank=True)
    text_answer = models.CharField(max_length=500, blank=True)
    is_correct = models.BooleanField(default=False)
    points_awarded = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "assessment_attempt_answer"
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "question"], name="uniq_attempt_answer"
            )
        ]


class TestSkillResult(BaseModel):
    """Per-skill breakdown of an attempt — the input to SkillEvidence."""

    attempt = models.ForeignKey(
        TestAttempt, on_delete=models.CASCADE, related_name="skill_results"
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="test_results"
    )
    percentage = models.PositiveSmallIntegerField(default=0)
    questions_total = models.PositiveSmallIntegerField(default=0)
    questions_correct = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "assessment_test_skill_result"
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "skill"], name="uniq_attempt_skill_result"
            )
        ]
