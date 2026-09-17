"""Account services.

Business rules live here rather than in views so registration can also be
driven from management commands, the seeder and tests.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.enums import Role
from apps.common.exceptions import DomainError, NotAllowed

from .models import (
    REQUIRED_CONSENTS,
    Consent,
    ConsentType,
    EmailVerification,
    GuardianLink,
    GuardianStatus,
    PasswordResetToken,
    User,
    calculate_age,
)

#: Roles a person may self-register as. ADMIN is deliberately absent — it is
#: granted from the Django admin, never claimed at the registration endpoint.
SELF_SERVICE_ROLES = frozenset({Role.STUDENT, Role.EMPLOYER})

TOKEN_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@transaction.atomic
def register_user(
    *,
    email: str,
    password: str,
    role: str,
    preferred_language: str = "uz",
    birth_date: date | None = None,
    phone: str | None = None,
    consents: list[str] | None = None,
    ip: str | None = None,
    user_agent: str = "",
    **profile_fields,
) -> User:
    """Create an account with its role profile and consent records."""
    if role not in SELF_SERVICE_ROLES:
        raise NotAllowed("This role cannot be self-registered.", code="role_not_allowed")

    granted = set(consents or [])
    missing = [c for c in REQUIRED_CONSENTS if c not in granted]
    if missing:
        raise DomainError(
            "Required consents are missing.",
            code="consent_required",
            details={"missing": missing},
        )

    if role == Role.STUDENT:
        _validate_student_age(birth_date)

    user = User.objects.create_user(
        email=email,
        password=password,
        role=role,
        preferred_language=preferred_language,
        phone=phone or None,
    )

    for consent_type in granted:
        if consent_type in ConsentType.values:
            Consent.objects.create(
                user=user,
                type=consent_type,
                granted=True,
                ip=ip,
                user_agent=user_agent[:400],
            )

    _create_role_profile(user, birth_date=birth_date, **profile_fields)
    return user


def _validate_student_age(birth_date: date | None) -> None:
    if birth_date is None:
        raise DomainError(
            "Date of birth is required for student accounts.",
            code="birth_date_required",
        )
    age = calculate_age(birth_date)
    if age is None or age < settings.MINIMUM_AGE:
        raise DomainError(
            f"Minimum age for the platform is {settings.MINIMUM_AGE}.",
            code="below_minimum_age",
            details={"minimum_age": settings.MINIMUM_AGE},
        )
    if age > 100:
        raise DomainError("Date of birth looks invalid.", code="invalid_birth_date")


def _create_role_profile(user: User, *, birth_date: date | None = None, **fields):
    """Create the profile row matching the user's role.

    Imported lazily: apps.profiles depends on apps.accounts, so a module-level
    import would be circular.
    """
    from apps.profiles.models import EmployerProfile, StudentProfile
    from apps.profiles.services import generate_youth_id

    if user.role == Role.STUDENT:
        return StudentProfile.objects.create(
            user=user,
            youth_id=generate_youth_id(),
            first_name=fields.get("first_name", ""),
            last_name=fields.get("last_name", ""),
            birth_date=birth_date,
            region_id=fields.get("region_id"),
        )
    if user.role == Role.EMPLOYER:
        return EmployerProfile.objects.create(
            owner=user,
            legal_name=fields.get("legal_name") or fields.get("company_name", ""),
            brand_name=fields.get("company_name", ""),
            contact_email=user.email,
            region_id=fields.get("region_id"),
        )
    return None


def requires_guardian_approval(user: User) -> bool:
    """True when a student is a minor and has no approved guardian yet."""
    if user.role != Role.STUDENT:
        return False
    profile = getattr(user, "student_profile", None)
    age = calculate_age(getattr(profile, "birth_date", None))
    if age is None or age >= settings.AGE_OF_MAJORITY:
        return False
    return not GuardianLink.objects.filter(
        minor=user, status=GuardianStatus.APPROVED
    ).exists()


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------
def grant_consent(user: User, consent_type: str, *, ip=None, user_agent="") -> Consent:
    if consent_type not in ConsentType.values:
        raise DomainError("Unknown consent type.", code="unknown_consent")

    consent, created = Consent.objects.get_or_create(
        user=user,
        type=consent_type,
        version="1.0",
        defaults={"ip": ip, "user_agent": user_agent[:400]},
    )
    if not created and not consent.is_active:
        consent.granted = True
        consent.revoked_at = None
        consent.granted_at = timezone.now()
        consent.save(update_fields=["granted", "revoked_at", "granted_at", "updated_at"])
    return consent


def revoke_consent(user: User, consent_type: str) -> None:
    if consent_type in REQUIRED_CONSENTS:
        raise DomainError(
            "This consent cannot be revoked while the account is active. "
            "Delete the account instead.",
            code="consent_required",
        )
    Consent.objects.filter(user=user, type=consent_type, revoked_at__isnull=True).update(
        granted=False, revoked_at=timezone.now()
    )


def has_consent(user: User, consent_type: str) -> bool:
    return Consent.objects.filter(
        user=user, type=consent_type, granted=True, revoked_at__isnull=True
    ).exists()


# ---------------------------------------------------------------------------
# One-time tokens
# ---------------------------------------------------------------------------
def create_email_verification(user: User) -> str:
    raw = secrets.token_urlsafe(32)
    EmailVerification.objects.create(
        user=user, token_hash=hash_token(raw), expires_at=timezone.now() + TOKEN_TTL
    )
    return raw


def confirm_email(raw_token: str) -> User:
    record = EmailVerification.objects.filter(token_hash=hash_token(raw_token)).first()
    if record is None or not record.is_usable:
        raise DomainError("Verification link is invalid or expired.", code="invalid_token")

    record.used_at = timezone.now()
    record.save(update_fields=["used_at", "updated_at"])

    user = record.user
    if not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified", "updated_at"])
    return user


def create_password_reset(user: User, ip=None) -> str:
    # Invalidate outstanding tokens so a stale link cannot be replayed later.
    PasswordResetToken.objects.filter(user=user, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    raw = secrets.token_urlsafe(32)
    PasswordResetToken.objects.create(
        user=user,
        token_hash=hash_token(raw),
        expires_at=timezone.now() + PASSWORD_RESET_TTL,
        ip=ip,
    )
    return raw


@transaction.atomic
def reset_password(raw_token: str, new_password: str) -> User:
    record = (
        PasswordResetToken.objects.select_for_update()
        .filter(token_hash=hash_token(raw_token))
        .first()
    )
    if record is None or not record.is_usable:
        raise DomainError("Reset link is invalid or expired.", code="invalid_token")

    record.used_at = timezone.now()
    record.save(update_fields=["used_at", "updated_at"])

    user = record.user
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])

    _revoke_all_sessions(user)
    return user


def change_password(user: User, current_password: str, new_password: str) -> None:
    if not user.check_password(current_password):
        raise DomainError("Current password is incorrect.", code="invalid_password")
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    _revoke_all_sessions(user)


def _revoke_all_sessions(user: User) -> None:
    """Blacklist every outstanding refresh token for this user.

    A password change must log out sessions the attacker may still hold.
    """
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)
