"""Signing in from somewhere new tells the account holder.

The alert is only useful if it stays rare. Two failure modes are tested here
as carefully as the happy path: a first-ever sign-in must not raise it (there
is nothing to compare against), and a browser updating itself must not raise it
either — an alert that cries wolf every few weeks is one nobody reads by the
time it matters.
"""

import pytest
from django.core import mail

from apps.accounts.devices import describe
from apps.accounts.models import LoginDevice
from apps.notifications.models import Notification, NotificationPreference, NotificationType

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/login/"

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
CHROME_WINDOWS_UPDATED = CHROME_WINDOWS.replace("Chrome/141", "Chrome/147")
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def sign_in(api, user, user_agent):
    return api.post(
        URL,
        {"email": user.email, "password": "TestPass12345"},
        format="json",
        HTTP_USER_AGENT=user_agent,
    )


def alerts_for(user):
    return Notification.objects.filter(user=user, type=NotificationType.NEW_DEVICE_LOGIN)


# --------------------------------------------------------------------------
# What counts as a device
# --------------------------------------------------------------------------
def test_a_browser_update_is_not_a_new_device():
    """The property the whole feature rests on: versions are not identity."""
    assert describe(CHROME_WINDOWS).fingerprint == describe(CHROME_WINDOWS_UPDATED).fingerprint


def test_a_different_kind_of_device_has_a_different_fingerprint():
    assert describe(CHROME_WINDOWS).fingerprint != describe(SAFARI_IPHONE).fingerprint


def test_user_agents_are_read_into_something_a_person_can_read():
    windows = describe(CHROME_WINDOWS)
    assert (windows.browser, windows.system, windows.kind) == ("Chrome", "Windows", "computer")

    iphone = describe(SAFARI_IPHONE)
    assert (iphone.browser, iphone.system, iphone.kind) == ("Safari", "iOS", "phone")


def test_an_empty_user_agent_is_one_device_not_a_new_one_each_time():
    assert describe("").fingerprint == describe(None).fingerprint
    assert describe("").is_recognised is False


# --------------------------------------------------------------------------
# When the alert fires
# --------------------------------------------------------------------------
def test_the_first_sign_in_records_the_device_but_raises_nothing(api, student):
    mail.outbox.clear()

    assert sign_in(api, student, CHROME_WINDOWS).status_code == 200

    assert LoginDevice.objects.filter(user=student).count() == 1
    assert not alerts_for(student).exists()
    assert mail.outbox == []


def test_signing_in_again_from_the_same_device_raises_nothing(api, student):
    sign_in(api, student, CHROME_WINDOWS)
    mail.outbox.clear()

    sign_in(api, student, CHROME_WINDOWS)

    assert LoginDevice.objects.filter(user=student).count() == 1
    assert not alerts_for(student).exists()
    assert mail.outbox == []


def test_an_updated_browser_raises_nothing(api, student):
    sign_in(api, student, CHROME_WINDOWS)
    mail.outbox.clear()

    sign_in(api, student, CHROME_WINDOWS_UPDATED)

    assert LoginDevice.objects.filter(user=student).count() == 1
    assert not alerts_for(student).exists()
    assert mail.outbox == []


def test_a_second_device_notifies_and_emails(api, student):
    sign_in(api, student, CHROME_WINDOWS)
    mail.outbox.clear()

    assert sign_in(api, student, SAFARI_IPHONE).status_code == 200

    assert LoginDevice.objects.filter(user=student).count() == 2

    alert = alerts_for(student).get()
    assert alert.title_key == "notifications.security.newDevice.title"
    assert alert.body_key == "notifications.security.newDevice.body"
    assert alert.payload["browser"] == "Safari"
    assert alert.payload["system"] == "iOS"
    assert alert.payload["ip"]

    assert len(mail.outbox) == 1
    assert student.email in mail.outbox[0].to
    # The mail has to name the device, or it tells the reader nothing they can
    # act on.
    assert "Safari" in mail.outbox[0].body
    assert "iOS" in mail.outbox[0].body


def test_an_unrecognisable_device_gets_its_own_wording(api, student):
    sign_in(api, student, CHROME_WINDOWS)
    mail.outbox.clear()

    sign_in(api, student, "some-native-client/1.0")

    alert = alerts_for(student).get()
    assert alert.body_key == "notifications.security.newDevice.bodyUnknown"


def test_the_device_row_keeps_the_latest_address_and_agent(api, student):
    sign_in(api, student, CHROME_WINDOWS)
    sign_in(api, student, CHROME_WINDOWS_UPDATED)

    device = LoginDevice.objects.get(user=student)
    assert device.user_agent == CHROME_WINDOWS_UPDATED
    assert device.last_ip


def test_one_switch_silences_both_channels(api, student):
    """Turning the notification off must not leave the email arriving anyway."""
    sign_in(api, student, CHROME_WINDOWS)
    NotificationPreference.objects.create(
        user=student, type=NotificationType.NEW_DEVICE_LOGIN, enabled=False
    )
    mail.outbox.clear()

    sign_in(api, student, SAFARI_IPHONE)

    assert not alerts_for(student).exists()
    assert mail.outbox == []
    # The device is still recorded — silencing the alert is not the same as
    # forgetting where the account is used.
    assert LoginDevice.objects.filter(user=student).count() == 2


def test_two_accounts_do_not_share_devices(api, student, other_student):
    sign_in(api, student, CHROME_WINDOWS)
    mail.outbox.clear()

    # The same browser, a different account: first device for that account, so
    # nothing is raised.
    sign_in(api, other_student, CHROME_WINDOWS)

    assert not alerts_for(other_student).exists()
    assert mail.outbox == []
    assert LoginDevice.objects.filter(user=other_student).count() == 1


def test_the_email_is_written_in_the_account_holders_language(api, student):
    """The console backend mangles Cyrillic when stdout is redirected to a log,
    which looks like a broken email and is not one. This checks the message
    Django actually built, rather than what a Windows console could print."""
    student.preferred_language = "ru"
    student.save(update_fields=["preferred_language"])

    sign_in(api, student, CHROME_WINDOWS)
    mail.outbox.clear()
    sign_in(api, student, SAFARI_IPHONE)

    message = mail.outbox[0]
    assert "\u0432\u0445\u043e\u0434 \u0441 \u043d\u043e\u0432\u043e\u0433\u043e" in message.subject
    assert "\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435" in message.body
    assert "IP-\u0430\u0434\u0440\u0435\u0441" in message.body
    assert "\ufffd" not in message.body

    # And it survives being encoded for the wire, which is the part a console
    # log cannot show: get_payload(decode=True) undoes the transfer encoding
    # and hands back the bytes an SMTP server would carry.
    wire = message.message().get_payload(decode=True)
    assert "смените пароль".encode("utf-8") in wire
