"""Email OTP delivery and verification, serialized per account in PostgreSQL."""

import logging
import math
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.common.exceptions import DomainError

from .models import EmailVerification, User

logger = logging.getLogger(__name__)
CODE_TTL = timedelta(minutes=10)
RESEND_DELAY = timedelta(minutes=1)
SEND_WINDOW = timedelta(hours=1)
MAX_SENDS = 5
MAX_ATTEMPTS = 5

COPY = {
    "en": (
        "Verify your email",
        "Your verification code is:\n\n{code}\n\n"
        "It expires in 10 minutes. Never share this code.\n"
        "If you did not request it, ignore this email.",
    ),
    "uz": (
        "Email manzilingizni tasdiqlang",
        "Tasdiqlash kodingiz:\n\n{code}\n\n"
        "Kod 10 daqiqa amal qiladi. Uni hech kimga bermang.\n"
        "Agar kodni siz so'ramagan bo'lsangiz, bu xatga e'tibor bermang.",
    ),
    "ru": (
        "Подтвердите email",
        "Ваш код подтверждения:\n\n{code}\n\n"
        "Код действует 10 минут. Никому его не сообщайте.\n"
        "Если вы не запрашивали код, проигнорируйте это письмо.",
    ),
}


class VerificationRateLimited(DomainError):
    status_code = 429
    code = "verification_rate_limited"
    message = "Please wait before requesting another verification code."


def _digest(record, code):
    # The account/challenge IDs prevent collisions; SECRET_KEY prevents offline
    # enumeration of the million possible codes from a leaked database alone.
    return salted_hmac(
        "accounts.email-otp", f"{record.user_id}:{record.pk}:{code}", algorithm="sha256"
    ).hexdigest()


def _seconds(delta):
    return max(0, math.ceil(delta.total_seconds()))


def _resend_after(user, latest, now):
    delay = _seconds(latest.created_at + RESEND_DELAY - now) if latest else 0
    recent = EmailVerification.objects.filter(
        user=user, created_at__gt=now - SEND_WINDOW
    ).order_by("created_at")
    if recent.count() >= MAX_SENDS:
        delay = max(delay, _seconds(recent.first().created_at + SEND_WINDOW - now))
    return delay


def verification_status(user):
    latest = user.email_verifications.order_by("-created_at", "-pk").first()
    now = timezone.now()
    usable = latest is not None and latest.is_usable and not user.email_verified
    return {
        "email": user.email,
        "email_verified": user.email_verified,
        "code_sent": bool(usable and latest.sent_at),
        "expires_in": _seconds(latest.expires_at - now) if usable else 0,
        "resend_after": _resend_after(user, latest, now) if not user.email_verified else 0,
        "attempts_remaining": MAX_ATTEMPTS - latest.attempts if usable else 0,
    }


def send_verification_code(user):
    """Reserve a send under a row lock; perform bounded SMTP outside the lock.

    A failed/uncertain SMTP attempt still consumes its send budget. The code
    stays valid in case the provider accepted the mail before disconnecting.
    No response or application log ever includes the OTP.
    """
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk, is_active=True)
        if user.email_verified:
            return verification_status(user)
        now = timezone.now()
        latest = user.email_verifications.order_by("-created_at", "-pk").first()
        retry_after = _resend_after(user, latest, now)
        if retry_after:
            raise VerificationRateLimited(details={"retry_after": retry_after})
        user.email_verifications.filter(used_at__isnull=True).update(used_at=now)
        code = f"{secrets.randbelow(1_000_000):06d}"
        record = EmailVerification(user=user, expires_at=now + CODE_TTL)
        record.token_hash = _digest(record, code)
        record.save()

    subject, body = COPY.get(user.preferred_language, COPY["en"])
    try:
        with get_connection(timeout=settings.EMAIL_TIMEOUT) as connection:
            sent = send_mail(
                subject=f"{settings.PLATFORM_NAME} — {subject}",
                message=body.format(code=code),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
                connection=connection,
            )
        if sent == 1:
            EmailVerification.objects.filter(pk=record.pk).update(sent_at=timezone.now())
        else:
            logger.warning("Verification email not accepted for account %s", user.pk)
    except Exception as exc:
        # Do not log provider responses, credentials, message bodies or codes.
        logger.warning("Verification delivery failed for account %s (%s)", user.pk, type(exc).__name__)
    return verification_status(user)


def ensure_verification_code(user):
    """Sign-in resumes an existing challenge without sending on every reload."""
    state = verification_status(user)
    if not user.email_verified and not state["expires_in"] and not state["resend_after"]:
        try:
            return send_verification_code(user)
        except VerificationRateLimited:
            # A concurrent sign-in/resend may have won the account lock.
            return verification_status(user)
    return state


def confirm_verification_code(user, code):
    error = None
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk, is_active=True)
        latest = user.email_verifications.order_by("-created_at", "-pk").first()
        if user.email_verified:
            error = DomainError("Email is already verified.", code="email_already_verified")
        elif latest is None or not latest.is_usable:
            error = DomainError("Request a new verification code.", code="verification_code_expired")
        elif not constant_time_compare(latest.token_hash, _digest(latest, code)):
            latest.attempts += 1
            if latest.attempts >= MAX_ATTEMPTS:
                latest.used_at = timezone.now()
            latest.save(update_fields=["attempts", "used_at", "updated_at"])
            error = DomainError(
                "The verification code is incorrect.",
                code="verification_code_invalid",
                details={"attempts_remaining": MAX_ATTEMPTS - latest.attempts},
            )
        else:
            latest.used_at = timezone.now()
            latest.save(update_fields=["used_at", "updated_at"])
            user.email_verified = True
            user.save(update_fields=["email_verified", "updated_at"])
    # Raising inside the transaction would roll back the failed-guess counter.
    if error:
        raise error
    return user
