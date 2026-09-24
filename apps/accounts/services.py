"""Account services.

Business rules live here rather than in views so registration can also be
driven from management commands, the seeder and tests.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.enums import Role
from apps.common.exceptions import DomainError, NotAllowed

from .devices import describe
from .models import (
    REQUIRED_CONSENTS,
    Consent,
    ConsentType,
    EmailVerification,
    GuardianLink,
    GuardianStatus,
    LoginDevice,
    PasswordResetToken,
    User,
    calculate_age,
)

logger = logging.getLogger(__name__)

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


#: New-device emails one account may receive in an hour. Every new device is
#: still recorded and shown in the app; only the mail is capped. Somebody with
#: the password could otherwise sign in from a stream of invented browsers and
#: turn the alert into a mail bomb — and teach the owner to ignore it.
NEW_DEVICE_EMAILS_PER_HOUR = 3

SETTINGS_PATH_BY_ROLE = {
    Role.STUDENT: "/student/settings",
    Role.EMPLOYER: "/employer/settings",
    Role.ADMIN: "/admin/settings",
}

# Written out per language rather than run through gettext: the project has no
# compiled message catalogues, so a gettext call here would render English to
# everybody while looking as though it had been translated.
NEW_DEVICE_EMAIL = {
    "uz": {
        "subject": "hisobingizga yangi qurilmadan kirildi",
        "greeting": "Assalomu alaykum!",
        "lead": "Hisobingizga biz ilgari ko'rmagan qurilmadan kirildi:",
        "device": "Qurilma",
        "ip": "IP manzil",
        "when": "Vaqt",
        "unknown": "aniqlanmadi",
        "ok": "Agar bu siz bo'lsangiz, hech narsa qilish shart emas.",
        "not_ok": "Agar bu siz bo'lmasangiz, darhol parolni o'zgartiring:",
    },
    "ru": {
        "subject": "вход с нового устройства",
        "greeting": "Здравствуйте!",
        "lead": "В ваш аккаунт вошли с устройства, которое мы раньше не видели:",
        "device": "Устройство",
        "ip": "IP-адрес",
        "when": "Время",
        "unknown": "не определено",
        "ok": "Если это были вы, делать ничего не нужно.",
        "not_ok": "Если это были не вы, сразу смените пароль:",
    },
    "en": {
        "subject": "a new device signed in",
        "greeting": "Hello,",
        "lead": "Your account was signed in to from a device we have not seen before:",
        "device": "Device",
        "ip": "IP address",
        "when": "Time",
        "unknown": "not recognised",
        "ok": "If this was you, there is nothing to do.",
        "not_ok": "If it was not you, change your password now:",
    },
}


def remember_login_device(user: User, request) -> tuple[LoginDevice, bool]:
    """Record the device behind this sign-in, and say whether it is a new one.

    "New" means two things together: this account has been used from somewhere
    before, and this is not one of those places. A first-ever device is not new
    — there is nothing to compare it against, and telling somebody that their
    own first sign-in looks suspicious is how an alert stops being read.

    This never raises. A sign-in that has already been authenticated must not
    fail because the thing that writes a notification did.
    """
    from apps.common.context import get_client_ip

    try:
        user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:400]
        ip = get_client_ip(request) or None
        shape = describe(user_agent)

        seen_before = LoginDevice.objects.filter(user=user).exists()

        device, created = LoginDevice.objects.get_or_create(
            user=user,
            fingerprint=shape.fingerprint,
            defaults={
                "browser": shape.browser,
                "system": shape.system,
                "kind": shape.kind,
                "user_agent": user_agent,
                "last_ip": ip,
            },
        )
        if not created:
            device.user_agent = user_agent or device.user_agent
            device.last_ip = ip or device.last_ip
            device.last_seen_at = timezone.now()
            device.save(
                update_fields=["user_agent", "last_ip", "last_seen_at", "updated_at"]
            )

        is_new = created and seen_before
        if is_new:
            _announce_new_device(user, shape, ip)
        return device, is_new
    except Exception:  # pragma: no cover - defence, not control flow
        logger.exception("Could not record the login device for user=%s", user.id)
        return None, False


def _announce_new_device(user: User, shape, ip: str | None) -> None:
    from apps.common.enums import Priority
    from apps.notifications.models import Notification, NotificationType
    from apps.notifications.services import notify

    # Counted before this alert is written, so the cap is the number of emails
    # already sent this hour rather than one fewer.
    sent_this_hour = Notification.objects.filter(
        user=user,
        type=NotificationType.NEW_DEVICE_LOGIN,
        created_at__gte=timezone.now() - timedelta(hours=1),
    ).count()

    notification = notify(
        user=user,
        type=NotificationType.NEW_DEVICE_LOGIN,
        title_key="notifications.security.newDevice.title",
        # Browser and system are proper nouns and are not translated, so the
        # body can interpolate them directly. When the user agent said nothing
        # recognisable there is a second phrasing rather than a sentence with
        # two blanks in it.
        body_key=(
            "notifications.security.newDevice.body"
            if shape.is_recognised
            else "notifications.security.newDevice.bodyUnknown"
        ),
        payload={
            "browser": shape.browser,
            "system": shape.system,
            "ip": ip or "",
        },
        action_url=SETTINGS_PATH_BY_ROLE.get(user.role, ""),
        priority=Priority.HIGH,
    )

    # One switch for both channels. If somebody has turned this notification
    # off, mailing them anyway ignores the same decision a second time.
    if notification is None:
        return
    if sent_this_hour >= NEW_DEVICE_EMAILS_PER_HOUR:
        logger.warning(
            "New-device email suppressed for user=%s: %s already sent this hour.",
            user.id,
            sent_this_hour,
        )
        return
    _send_new_device_email(user, shape, ip)


def _send_new_device_email(user: User, shape, ip: str | None) -> None:
    """Tell the account holder out of band.

    In-app is not enough on its own for this one: the person who needs to read
    it may not be the person holding the session that triggered it.
    """
    from django.core.mail import send_mail

    copy = NEW_DEVICE_EMAIL.get(user.preferred_language, NEW_DEVICE_EMAIL["en"])
    link = settings.FRONTEND_URL + SETTINGS_PATH_BY_ROLE.get(user.role, "")
    when = timezone.localtime().strftime("%d.%m.%Y %H:%M")

    body = "\n".join(
        [
            copy["greeting"],
            "",
            copy["lead"],
            "",
            f"  {copy['device']}: {shape.label() or copy['unknown']}",
            f"  {copy['ip']}: {ip or copy['unknown']}",
            f"  {copy['when']}: {when}",
            "",
            copy["ok"],
            copy["not_ok"],
            f"  {link}",
            "",
        ]
    )

    send_mail(
        subject=f"{settings.PLATFORM_NAME} — {copy['subject']}",
        message=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@yoshlarkapitali.uz"),
        recipient_list=[user.email],
        fail_silently=True,
    )


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
