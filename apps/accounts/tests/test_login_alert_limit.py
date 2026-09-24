"""A sign-in alert must not become a way to flood somebody's inbox.

Each new kind of device sends the account holder an email. Somebody holding the
password could sign in from a stream of invented user agents — every one a
"new device" — and turn the alert into a mail bomb, which also teaches the
owner to ignore it on the day it matters. The emails are therefore capped per
hour. The in-app notifications are not: they cost nothing to deliver, and a
burst of them is exactly what the owner should see when they next open the app.
"""

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.services import NEW_DEVICE_EMAILS_PER_HOUR
from apps.notifications.models import Notification, NotificationType

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"

#: Seven genuinely different device shapes: browser, system and kind all vary.
DEVICES = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36 Edg/141.0",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36 OPR/112.0",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36",
]


def _sign_in(api, user, agent):
    response = api.post(
        LOGIN,
        {"email": user.email, "password": "TestPass12345"},
        format="json",
        HTTP_USER_AGENT=agent,
    )
    assert response.status_code == 200, response.data


def _alerts(user):
    return Notification.objects.filter(user=user, type=NotificationType.NEW_DEVICE_LOGIN)


def test_a_burst_of_new_devices_sends_only_a_few_emails(api, student):
    _sign_in(api, student, DEVICES[0])  # the first device is not "new"
    mail.outbox.clear()

    for agent in DEVICES[1:]:
        _sign_in(api, student, agent)

    assert len(mail.outbox) == NEW_DEVICE_EMAILS_PER_HOUR
    # Every one of them is still on the record in the app.
    assert _alerts(student).count() == len(DEVICES) - 1


def test_the_allowance_comes_back_after_an_hour(api, student):
    _sign_in(api, student, DEVICES[0])
    for agent in DEVICES[1 : 1 + NEW_DEVICE_EMAILS_PER_HOUR]:
        _sign_in(api, student, agent)
    mail.outbox.clear()

    # The earlier alerts are now more than an hour old.
    _alerts(student).update(created_at=timezone.now() - timezone.timedelta(minutes=61))
    _sign_in(api, student, DEVICES[-1])

    assert len(mail.outbox) == 1


def test_one_persons_burst_does_not_silence_anothers_alert(api, student, other_student):
    _sign_in(api, student, DEVICES[0])
    for agent in DEVICES[1:]:
        _sign_in(api, student, agent)

    _sign_in(api, other_student, DEVICES[0])
    mail.outbox.clear()
    _sign_in(api, other_student, DEVICES[1])

    assert len(mail.outbox) == 1
    assert other_student.email in mail.outbox[0].to
