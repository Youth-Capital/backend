"""Identity, roles and consent."""

from __future__ import annotations

from datetime import date

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.enums import Language, Role
from apps.common.models import BaseModel

phone_validator = RegexValidator(
    regex=r"^\+?[0-9]{9,15}$",
    message=_("Enter a phone number in international format, e.g. +998901234567."),
)


class UserManager(models.Manager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.full_clean(exclude=["password"], validate_unique=False)
        user.save(using=self._db)
        return user

    @staticmethod
    def normalize_email(email: str) -> str:
        try:
            local, _sep, domain = email.strip().rpartition("@")
        except ValueError:  # pragma: no cover
            return email
        return f"{local}@{domain.lower()}" if local else email

    def create_user(self, email, password=None, **extra):
        extra.setdefault("role", Role.STUDENT)
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("role", Role.ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        extra.setdefault("email_verified", True)
        if extra["is_staff"] is not True or extra["is_superuser"] is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra)

    def get_by_natural_key(self, username):
        return self.get(email__iexact=username)


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    """Single user table for all four roles.

    One table rather than four keeps authentication, consent and audit uniform;
    role-specific data lives in the profile models under `apps.profiles`.
    """

    email = models.EmailField(_("email"), max_length=254, unique=True)
    phone = models.CharField(
        max_length=20, unique=True, null=True, blank=True, validators=[phone_validator]
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STUDENT)
    preferred_language = models.CharField(
        max_length=2, choices=Language.choices, default=Language.UZ
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)

    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"
        indexes = [
            models.Index(fields=["role", "is_active"]),
            models.Index(fields=["date_joined"]),
        ]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        if self.phone == "":
            self.phone = None  # keep the unique index usable for blank phones
        super().save(*args, **kwargs)

    # -- role helpers ----------------------------------------------------
    @property
    def is_student(self) -> bool:
        return self.role == Role.STUDENT

    @property
    def is_employer(self) -> bool:
        return self.role == Role.EMPLOYER

    @property
    def is_mentor(self) -> bool:
        return self.role == Role.MENTOR

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN or self.is_superuser

    @property
    def display_name(self) -> str:
        profile = getattr(self, "student_profile", None)
        if profile and (profile.first_name or profile.last_name):
            return f"{profile.first_name} {profile.last_name}".strip()
        company = getattr(self, "employer_profile", None)
        if company:
            return company.brand_name or company.legal_name
        return self.email.split("@")[0]


class ConsentType(models.TextChoices):
    TERMS = "TERMS", _("Terms of service")
    PRIVACY = "PRIVACY", _("Privacy policy")
    DATA_PROCESSING = "DATA_PROCESSING", _("Personal data processing")
    AI_PROCESSING = "AI_PROCESSING", _("AI-assisted analysis of my profile")
    TALENT_SEARCH = "TALENT_SEARCH", _("Visibility to employers in talent search")
    INCOME_TRACKING = "INCOME_TRACKING", _("Income outcome tracking")
    MARKETING = "MARKETING", _("Marketing communication")


#: Consents a user must hold before the account becomes usable.
REQUIRED_CONSENTS = (
    ConsentType.TERMS,
    ConsentType.PRIVACY,
    ConsentType.DATA_PROCESSING,
)


class Consent(BaseModel):
    """Explicit, versioned, revocable consent (TZ §12).

    Rows are never updated in place — revoking writes `revoked_at`, granting a
    new policy version writes a new row. That history is what makes the consent
    defensible later.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="consents")
    type = models.CharField(max_length=32, choices=ConsentType.choices)
    version = models.CharField(max_length=16, default="1.0")
    granted = models.BooleanField(default=True)
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)

    class Meta:
        db_table = "accounts_consent"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "type", "version"], name="uniq_consent_user_type_version"
            )
        ]
        indexes = [models.Index(fields=["user", "type"])]

    def __str__(self) -> str:
        return f"{self.user_id} · {self.type} · {'granted' if self.is_active else 'revoked'}"

    @property
    def is_active(self) -> bool:
        return self.granted and self.revoked_at is None


class GuardianStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    APPROVED = "APPROVED", _("Approved")
    REVOKED = "REVOKED", _("Revoked")


class GuardianLink(BaseModel):
    """Legal representative of a minor.

    The platform admits schoolchildren, so an under-18 account needs a guardian
    approval before its data is processed (TZ §12 "yoshga bog'liq ruxsat").
    """

    minor = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="guardian_links"
    )
    guardian = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="ward_links"
    )
    relation = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16, choices=GuardianStatus.choices, default=GuardianStatus.PENDING
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_guardian_link"
        constraints = [
            models.UniqueConstraint(
                fields=["minor", "guardian"], name="uniq_guardian_minor"
            ),
            models.CheckConstraint(
                condition=~models.Q(minor=models.F("guardian")),
                name="guardian_is_not_minor",
            ),
        ]


class EmailVerification(BaseModel):
    """Only a hash is stored: a leaked database dump must not hand over
    working verification links."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="email_verifications"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_email_verification"

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()


class PasswordResetToken(BaseModel):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="password_resets"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "accounts_password_reset"

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()


def calculate_age(birth_date: date | None, on: date | None = None) -> int | None:
    if not birth_date:
        return None
    on = on or timezone.localdate()
    return (
        on.year
        - birth_date.year
        - ((on.month, on.day) < (birth_date.month, birth_date.day))
    )
