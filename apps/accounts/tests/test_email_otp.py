"""OTP ownership proof, server-side gating and bounded guessing/sending."""

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.mail import get_connection
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.accounts.authentication import issue_tokens
from apps.accounts.email_verification import (
    VerificationRateLimited,
    confirm_verification_code,
    send_verification_code,
)
from apps.accounts.models import EmailVerification, User
from apps.common.exceptions import DomainError

pytestmark = pytest.mark.django_db
VERIFY = "/api/v1/auth/email/verify/"
RESEND = "/api/v1/auth/email/resend/"
STATUS = "/api/v1/auth/email/verification/"


def register(api, **headers):
    return api.post("/api/v1/auth/register/", {
        "email": "otp.learner@example.com", "password": "TestPass12345",
        "role": "STUDENT", "preferred_language": "en", "birth_date": "2000-01-01",
        "first_name": "QA", "consents": ["TERMS", "PRIVACY", "DATA_PROCESSING"],
    }, format="json", **headers)


def latest_code():
    return re.search(r"\b[0-9]{6}\b", mail.outbox[-1].body).group()


@pytest.fixture
def pending(api):
    with patch("apps.accounts.email_verification.secrets.randbelow", return_value=112233):
        response = register(api)
    assert response.status_code == 201, response.data
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    return User.objects.get(email="otp.learner@example.com")


def age_sends(user, *, seconds=61):
    user.email_verifications.update(created_at=timezone.now() - timedelta(seconds=seconds))


@pytest.mark.parametrize("header", [None, "1", "true"])
def test_registration_sends_code_only_by_email_and_never_returns_debug_token(api, header):
    headers = {"HTTP_X_DEBUG_RETURN_TOKEN": header} if header else {}
    with patch("apps.accounts.email_verification.secrets.randbelow", return_value=1234):
        response = register(api, **headers)
    assert response.status_code == 201, response.data
    assert "email_verification_token" not in response.data
    assert "001234" not in str(response.data)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["otp.learner@example.com"]
    assert latest_code() == "001234"
    record = EmailVerification.objects.get()
    assert record.token_hash != hashlib.sha256(b"001234").hexdigest()
    assert record.sent_at is not None
    assert response.data["verification"]["code_sent"] is True


def test_pending_session_only_has_verification_and_identity_access(api, pending):
    assert api.get("/api/v1/auth/me/").status_code == 200
    for path in ("/api/v1/me/profile/", "/api/v1/jobs/vacancies/", "/api/v1/ai/intake/", "/api/v1/auth/consents/"):
        response = api.get(path)
        assert response.status_code == 403, (path, response.data)
        assert response.data["error"]["code"] == "email_verification_required"
    state = api.get(STATUS)
    assert state.status_code == 200
    assert 0 < state.data["expires_in"] <= 600
    assert 0 < state.data["resend_after"] <= 60
    assert state.data["attempts_remaining"] == 5


def test_correct_code_unlocks_application_and_cannot_be_replayed(api, pending):
    code = latest_code()
    response = api.post(VERIFY, {"code": code}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["email_verified"] is True
    pending.refresh_from_db()
    assert pending.email_verified
    assert api.get("/api/v1/me/profile/").status_code == 200
    assert api.post(VERIFY, {"code": code}, format="json").status_code == 400


def test_five_wrong_guesses_persist_and_exhaust_the_code(api, pending):
    code = latest_code()
    wrong = "000000" if code != "000000" else "999999"
    for attempt in range(1, 6):
        response = api.post(VERIFY, {"code": wrong}, format="json")
        assert response.status_code == 400
        assert response.data["error"]["details"]["attempts_remaining"] == 5 - attempt
        assert EmailVerification.objects.get(user=pending).attempts == attempt
    response = api.post(VERIFY, {"code": code}, format="json")
    assert response.status_code == 400
    pending.refresh_from_db()
    assert not pending.email_verified


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "١٢٣٤٥٦", 123456, None])
def test_invalid_code_shape_is_rejected(api, pending, code):
    response = api.post(VERIFY, {"code": code}, format="json")
    # DRF accepts numeric JSON scalars as strings; exactly six digits still
    # count as a real guess and cannot bypass the attempt budget.
    assert response.status_code == 400


def test_expired_code_cannot_verify(api, pending):
    code = latest_code()
    pending.email_verifications.update(expires_at=timezone.now() - timedelta(seconds=1))
    response = api.post(VERIFY, {"code": code}, format="json")
    assert response.status_code == 400
    assert response.data["error"]["code"] == "verification_code_expired"


def test_resend_cooldown_cannot_be_bypassed_by_changing_ip(api, pending):
    for ip in ("192.0.2.1", "192.0.2.2"):
        response = api.post(RESEND, {}, format="json", REMOTE_ADDR=ip)
        assert response.status_code == 429
        assert int(response["Retry-After"]) > 0
    assert len(mail.outbox) == 1


def test_resend_invalidates_old_code_and_accepts_the_new_one(api, pending):
    old = latest_code()
    age_sends(pending)
    next_value = 123456 if old != "123456" else 654321
    with patch("apps.accounts.email_verification.secrets.randbelow", return_value=next_value):
        response = api.post(RESEND, {}, format="json")
    assert response.status_code == 200, response.data
    assert api.post(VERIFY, {"code": old}, format="json").status_code == 400
    assert api.post(VERIFY, {"code": latest_code()}, format="json").status_code == 200


def test_five_sends_per_account_per_hour(api, pending):
    for _ in range(4):
        age_sends(pending)
        assert api.post(RESEND, {}, format="json").status_code == 200
    age_sends(pending)
    response = api.post(RESEND, {}, format="json")
    assert response.status_code == 429
    assert int(response["Retry-After"]) > 3000
    assert len(mail.outbox) == 5


def test_mail_failure_preserves_account_and_reports_resendable_state(api, caplog):
    with patch("apps.accounts.email_verification.secrets.randbelow", return_value=1234), patch(
        "apps.accounts.email_verification.send_mail", side_effect=TimeoutError("SMTP unavailable")
    ):
        response = register(api, HTTP_X_DEBUG_RETURN_TOKEN="1")
    assert response.status_code == 201
    assert response.data["verification"]["code_sent"] is False
    assert User.objects.filter(email="otp.learner@example.com").exists()
    assert "001234" not in caplog.text
    assert "SMTP unavailable" not in caplog.text
    assert "email_verification_token" not in response.data


def test_mail_send_uses_bounded_timeout(api):
    with patch("apps.accounts.email_verification.get_connection", wraps=get_connection) as factory:
        assert register(api).status_code == 201
    factory.assert_called_once_with(timeout=10)


def test_refresh_of_pending_session_does_not_unlock_application(api, pending):
    response = api.post("/api/v1/auth/refresh/", {}, format="json", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert response.status_code == 200
    assert response.data["user"]["email_verified"] is False
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    assert api.get("/api/v1/me/profile/").status_code == 403


def test_login_resumes_pending_verification_without_duplicate_email(api, pending):
    response = api.post("/api/v1/auth/login/", {"email": pending.email, "password": "TestPass12345"}, format="json")
    assert response.status_code == 200
    assert response.data["user"]["email_verified"] is False
    assert len(mail.outbox) == 1


def test_login_sends_a_fresh_code_after_expiry(api, pending):
    age_sends(pending)
    pending.email_verifications.update(expires_at=timezone.now() - timedelta(seconds=1))
    response = api.post("/api/v1/auth/login/", {"email": pending.email, "password": "TestPass12345"}, format="json")
    assert response.status_code == 200
    assert len(mail.outbox) == 2


def test_verified_accounts_do_not_receive_more_codes(api, student):
    access, _ = issue_tokens(student)
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    response = api.post(RESEND, {}, format="json")
    assert response.status_code == 200
    assert response.data["email_verified"] is True
    assert mail.outbox == []


def test_anonymous_and_legacy_link_verification_are_rejected(api):
    assert api.post(VERIFY, {"token": "old-debug-token"}, format="json").status_code == 401
    assert api.post(RESEND, {}, format="json").status_code == 401


@pytest.mark.django_db(transaction=True)
def test_parallel_resends_reserve_only_one_send():
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock concurrency requires PostgreSQL.")
    user = User.objects.create_user(email="race@example.com", password="test-passphrase-42")

    def attempt():
        close_old_connections()
        try:
            send_verification_code(user)
            return "sent"
        except VerificationRateLimited:
            return "limited"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(4)))
    assert outcomes.count("sent") == 1
    assert EmailVerification.objects.filter(user=user).count() == 1


@pytest.mark.django_db(transaction=True)
def test_parallel_guesses_cannot_bypass_five_attempt_limit(api, pending):
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock concurrency requires PostgreSQL.")

    def guess():
        close_old_connections()
        try:
            confirm_verification_code(pending, "000000")
            return "verified"
        except DomainError:
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: guess(), range(8)))
    assert set(outcomes) == {"rejected"}
    record = EmailVerification.objects.get(user=pending)
    assert record.attempts == 5
    assert record.used_at is not None
    pending.refresh_from_db()
    assert not pending.email_verified
