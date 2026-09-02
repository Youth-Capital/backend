"""Role profiles, skills and their evidence."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.enums import (
    EVIDENCE_WEIGHTS,
    EvidenceSource,
    Language,
    ProficiencyBand,
    VerificationStatus,
)
from apps.common.models import BaseModel
from apps.common.validators import validate_image_upload
from apps.taxonomy.models import Profession, Region, Skill


class EducationStatus(models.TextChoices):
    SCHOOL = "SCHOOL", _("School student")
    COLLEGE = "COLLEGE", _("College / lyceum")
    UNIVERSITY = "UNIVERSITY", _("University student")
    GRADUATE = "GRADUATE", _("Graduate")
    NONE = "NONE", _("Not studying")


class EmploymentStatus(models.TextChoices):
    STUDYING = "STUDYING", _("Studying")
    LOOKING = "LOOKING", _("Looking for work")
    EMPLOYED = "EMPLOYED", _("Employed")
    SELF_EMPLOYED = "SELF_EMPLOYED", _("Self-employed / entrepreneur")
    NOT_LOOKING = "NOT_LOOKING", _("Not looking")


class Gender(models.TextChoices):
    MALE = "MALE", _("Male")
    FEMALE = "FEMALE", _("Female")
    UNDISCLOSED = "UNDISCLOSED", _("Prefer not to say")


class StudentProfile(BaseModel):
    """The learner profile — TZ §6 "Youth ID"."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    youth_id = models.CharField(max_length=24, unique=True, db_index=True)

    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    gender = models.CharField(
        max_length=12, choices=Gender.choices, default=Gender.UNDISCLOSED
    )
    avatar = models.ImageField(
        upload_to="avatars/students/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )
    bio = models.TextField(blank=True, max_length=2000)

    region = models.ForeignKey(
        Region, on_delete=models.SET_NULL, null=True, blank=True, related_name="students"
    )
    city = models.CharField(max_length=120, blank=True)

    education_status = models.CharField(
        max_length=16, choices=EducationStatus.choices, default=EducationStatus.NONE
    )
    institution = models.CharField(max_length=200, blank=True)
    study_year = models.PositiveSmallIntegerField(null=True, blank=True)

    #: What the learner said interests them, as taxonomy categories rather than
    #: free text — the recommender already reads categories, so an interest is
    #: immediately usable instead of being a string nothing can join on.
    interests = models.ManyToManyField(
        "taxonomy.SkillCategory", blank=True, related_name="interested_students"
    )

    target_profession = models.ForeignKey(
        Profession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targeting_students",
    )
    employment_status = models.CharField(
        max_length=16, choices=EmploymentStatus.choices, default=EmploymentStatus.STUDYING
    )
    open_to_work = models.BooleanField(default=True)
    #: [{"code": "en", "level": "B2"}, ...]
    languages = models.JSONField(default=list, blank=True)

    profile_completion = models.PositiveSmallIntegerField(default=0)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    diagnostics_completed_at = models.DateTimeField(null=True, blank=True)

    #: Scenario C of TZ §9 — accelerated track for high-potential youth.
    is_high_potential = models.BooleanField(default=False)
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "profiles_student"
        indexes = [
            models.Index(fields=["region"]),
            models.Index(fields=["target_profession"]),
            models.Index(fields=["open_to_work", "employment_status"]),
        ]

    def __str__(self) -> str:
        return self.full_name or self.youth_id

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age(self) -> int | None:
        from apps.accounts.models import calculate_age

        return calculate_age(self.birth_date)

    @property
    def is_minor(self) -> bool:
        age = self.age
        return age is not None and age < settings.AGE_OF_MAJORITY


class CompanySize(models.TextChoices):
    MICRO = "MICRO", _("1-9")
    SMALL = "SMALL", _("10-49")
    MEDIUM = "MEDIUM", _("50-249")
    LARGE = "LARGE", _("250+")


class EmployerProfile(BaseModel):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employer_profile",
    )
    legal_name = models.CharField(max_length=255)
    brand_name = models.CharField(max_length=255, blank=True)
    slug = models.SlugField(max_length=140, unique=True)
    tax_id = models.CharField(max_length=32, blank=True)

    industry = models.CharField(max_length=120, blank=True)
    size = models.CharField(
        max_length=8, choices=CompanySize.choices, default=CompanySize.SMALL
    )
    website = models.URLField(blank=True)
    logo = models.ImageField(
        upload_to="logos/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )
    description = models.TextField(blank=True, max_length=4000)

    region = models.ForeignKey(
        Region, on_delete=models.SET_NULL, null=True, blank=True, related_name="employers"
    )
    address = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)

    verification_status = models.CharField(
        max_length=12,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_employers",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "profiles_employer"
        indexes = [models.Index(fields=["verification_status", "is_active"])]

    def __str__(self) -> str:
        return self.brand_name or self.legal_name

    @property
    def display_name(self) -> str:
        return self.brand_name or self.legal_name

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VerificationStatus.VERIFIED


class EmployerMemberRole(models.TextChoices):
    OWNER = "OWNER", _("Owner")
    RECRUITER = "RECRUITER", _("Recruiter")
    TRAINER = "TRAINER", _("Trainer")


class EmployerMember(BaseModel):
    """Additional staff acting on behalf of a company."""

    company = models.ForeignKey(
        EmployerProfile, on_delete=models.CASCADE, related_name="members"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employer_roles"
    )
    role = models.CharField(
        max_length=12,
        choices=EmployerMemberRole.choices,
        default=EmployerMemberRole.RECRUITER,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "profiles_employer_member"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "user"], name="uniq_employer_member"
            )
        ]


class MentorProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mentor_profile",
    )
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    headline = models.CharField(max_length=200, blank=True)
    bio = models.TextField(blank=True, max_length=4000)
    avatar = models.ImageField(
        upload_to="avatars/mentors/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )

    expertise = models.ManyToManyField(Skill, blank=True, related_name="mentors")
    professions = models.ManyToManyField(Profession, blank=True, related_name="mentors")

    years_experience = models.PositiveSmallIntegerField(default=0)
    is_free = models.BooleanField(default=True)
    hourly_rate = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="UZS")
    languages = models.JSONField(default=list, blank=True)
    #: {"mon": [["09:00","12:00"]], ...}
    availability = models.JSONField(default=dict, blank=True)

    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    rating_count = models.PositiveIntegerField(default=0)
    sessions_count = models.PositiveIntegerField(default=0)
    accepting_students = models.BooleanField(default=True)

    verification_status = models.CharField(
        max_length=12,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )

    class Meta:
        db_table = "profiles_mentor"
        indexes = [models.Index(fields=["verification_status", "accepting_students"])]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or str(self.user_id)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class SkillStatus(models.TextChoices):
    DECLARED = "DECLARED", _("Declared")
    VERIFIED = "VERIFIED", _("Verified")


class UserSkill(BaseModel):
    """Aggregate of everything known about one user's one skill.

    `proficiency` is the confidence-weighted level; `confidence` says how much
    the platform trusts it. Matching multiplies by confidence so a wall of
    self-declared skills cannot outrank a tested one (docs/01-ANALYSIS.md §3.3).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="skills"
    )
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="user_links")

    proficiency = models.PositiveSmallIntegerField(default=0)
    confidence = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    status = models.CharField(
        max_length=10, choices=SkillStatus.choices, default=SkillStatus.DECLARED
    )
    best_source = models.CharField(
        max_length=12, choices=EvidenceSource.choices, default=EvidenceSource.SELF
    )
    last_evidence_at = models.DateTimeField(null=True, blank=True)
    is_highlighted = models.BooleanField(default=False)

    class Meta:
        db_table = "profiles_user_skill"
        ordering = ["-proficiency"]
        constraints = [
            models.UniqueConstraint(fields=["user", "skill"], name="uniq_user_skill"),
            models.CheckConstraint(
                condition=models.Q(proficiency__gte=0, proficiency__lte=100),
                name="user_skill_proficiency_range",
            ),
        ]
        indexes = [
            models.Index(fields=["skill", "status"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} · {self.skill.name} {self.proficiency}"

    @property
    def band(self) -> str:
        return ProficiencyBand.from_score(self.proficiency)

    @property
    def is_verified(self) -> bool:
        return self.status == SkillStatus.VERIFIED


class SkillEvidence(BaseModel):
    """One piece of proof behind a UserSkill.

    Kept as an append-only trail rather than overwriting the aggregate, so a
    score can always be explained: "88% because a published test said so in
    March, decayed by age".
    """

    user_skill = models.ForeignKey(
        UserSkill, on_delete=models.CASCADE, related_name="evidence"
    )
    source = models.CharField(max_length=12, choices=EvidenceSource.choices)
    score = models.PositiveSmallIntegerField(default=0)
    weight = models.DecimalField(max_digits=3, decimal_places=2, default=0.35)

    #: Soft reference to the originating object (course, test attempt, ...).
    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.UUIDField(null=True, blank=True)

    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_skill_evidence",
    )
    issued_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "profiles_skill_evidence"
        ordering = ["-issued_at"]
        indexes = [
            models.Index(fields=["user_skill", "issued_at"]),
            models.Index(fields=["ref_type", "ref_id"]),
        ]

    def save(self, *args, **kwargs):
        if not self.weight or float(self.weight) == 0.35:
            self.weight = EVIDENCE_WEIGHTS.get(self.source, 0.35)
        super().save(*args, **kwargs)


class Education(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="education"
    )
    institution = models.CharField(max_length=200)
    degree = models.CharField(max_length=120, blank=True)
    field_of_study = models.CharField(max_length=160, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=False)
    gpa = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    language = models.CharField(
        max_length=2, choices=Language.choices, default=Language.UZ
    )

    class Meta:
        db_table = "profiles_education"
        ordering = ["-is_current", "-start_date"]
