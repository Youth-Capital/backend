"""One request has one address, and the caller does not get to choose it.

Behind a reverse proxy the client's address arrives in a header, and a header
is the easiest thing in a request to write. Three layers were reading it
differently: the rate limiter hashed the whole of `X-Forwarded-For`, the audit
log and the new-device emails took its first entry, and the lockout after
repeated failed sign-ins ignored it and used `REMOTE_ADDR`, which behind a
proxy is the proxy.

That combination handed out two things it should not have. A caller who sent
`X-Forwarded-For: 10.0.0.1` had that address written into the audit log; by
changing one character per request it also had a fresh rate-limit bucket each
time, so the five-per-minute sign-in limit counted to one and started again.
Meanwhile the lockout saw every visitor as the same address, which made it a
lockout on the username alone.

All three now ask `apps.common.context.get_client_ip`, which counts from the
end of the chain that `TRUSTED_PROXY_COUNT` describes.
"""

import pytest
from django.core.cache import cache
from django.test import RequestFactory, override_settings

from apps.common.context import get_client_ip

factory = RequestFactory()

#: One proxy in front of us, as in production.
BEHIND_NGINX = {"TRUSTED_PROXY_COUNT": 1}
#: Nothing in front of us, as on a developer's machine and in this suite.
DIRECT = {"TRUSTED_PROXY_COUNT": 0}


def request_with(**meta):
    return factory.post("/api/v1/auth/login/", {}, **meta)


def throttle_key(request, *, num_proxies):
    """The rate-limit bucket DRF would use for this request."""
    from rest_framework.request import Request
    from rest_framework.throttling import AnonRateThrottle

    with override_settings(REST_FRAMEWORK={"NUM_PROXIES": num_proxies}):
        throttle = AnonRateThrottle()
        return throttle.get_ident(Request(request))


# ---------------------------------------------------------------------------
# Which address wins
# ---------------------------------------------------------------------------
@override_settings(**DIRECT)
def test_with_nothing_in_front_the_forwarded_header_is_ignored():
    """Nothing could have written it except the caller, so it is not evidence."""
    request = request_with(REMOTE_ADDR="203.0.113.7", HTTP_X_FORWARDED_FOR="1.2.3.4")

    assert get_client_ip(request) == "203.0.113.7"


@override_settings(**BEHIND_NGINX)
def test_behind_one_proxy_the_address_the_proxy_wrote_is_used():
    request = request_with(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.7")

    assert get_client_ip(request) == "203.0.113.7"


@override_settings(**BEHIND_NGINX)
def test_an_entry_the_caller_added_on_the_left_is_not_believed():
    """The shape of the attack: claim an address, let the proxy append the real one.

    nginx as configured in deploy/ overwrites the header, so this shape does
    not survive the proxy at all — but the arithmetic has to be right on its
    own, because the append form is what almost every nginx example writes.
    """
    request = request_with(
        REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.7"
    )

    assert get_client_ip(request) == "203.0.113.7"


@override_settings(TRUSTED_PROXY_COUNT=2)
def test_two_proxies_are_counted_from_the_right():
    request = request_with(
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.7, 198.51.100.9",
    )

    assert get_client_ip(request) == "203.0.113.7"


@override_settings(TRUSTED_PROXY_COUNT=2)
def test_a_header_shorter_than_the_chain_is_refused():
    """It cannot have come through the proxies we expect, so it proves nothing."""
    request = request_with(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4")

    assert get_client_ip(request) == "127.0.0.1"


@override_settings(**BEHIND_NGINX)
def test_padding_the_header_with_blanks_does_not_shift_the_count():
    request = request_with(
        REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR=" , ,203.0.113.7 , "
    )

    assert get_client_ip(request) == "203.0.113.7"


@override_settings(**BEHIND_NGINX)
def test_a_missing_header_falls_back_to_the_peer():
    assert get_client_ip(request_with(REMOTE_ADDR="127.0.0.1")) == "127.0.0.1"


# ---------------------------------------------------------------------------
# The layers agree with each other
# ---------------------------------------------------------------------------
@override_settings(**BEHIND_NGINX)
def test_the_rate_limiter_and_the_audit_log_pick_the_same_address():
    """The property that matters: one request cannot be two different clients."""
    request = request_with(
        REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.7"
    )

    assert get_client_ip(request) == throttle_key(request, num_proxies=1)


@override_settings(**BEHIND_NGINX)
def test_rotating_the_header_no_longer_buys_a_fresh_rate_limit_bucket():
    first = request_with(
        REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.7"
    )
    second = request_with(
        REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="10.0.0.99, 203.0.113.7"
    )

    assert throttle_key(first, num_proxies=1) == throttle_key(second, num_proxies=1)


def test_the_unset_configuration_is_what_gave_out_fresh_buckets():
    """A witness for the bug being fixed, not a requirement.

    With NUM_PROXIES unset, DRF hashes the whole header, so these two requests
    from one caller counted as two different clients. If this ever starts
    failing, DRF changed its identity rule and the settings need revisiting.
    """
    first = request_with(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="10.0.0.1")
    second = request_with(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="10.0.0.99")

    assert throttle_key(first, num_proxies=None) != throttle_key(
        second, num_proxies=None
    )


def test_the_throttles_count_in_the_configured_cache():
    """Ties the rate limits to the cache backend the deployment chooses.

    Which is why production insists on Redis: see test_cache_backend.py.
    """
    from django.core.cache import caches
    from rest_framework.throttling import SimpleRateThrottle

    SimpleRateThrottle.cache.set("proxy-ip-test-probe", "counted", 30)
    try:
        assert caches["default"].get("proxy-ip-test-probe") == "counted"
    finally:
        caches["default"].delete("proxy-ip-test-probe")


# ---------------------------------------------------------------------------
# The lockout after failed sign-ins
# ---------------------------------------------------------------------------
def test_axes_asks_the_same_function_as_everything_else():
    from django.conf import settings

    assert settings.AXES_CLIENT_IP_CALLABLE == "apps.common.context.get_client_ip"


@override_settings(**BEHIND_NGINX)
def test_axes_resolves_the_address_the_proxy_wrote():
    from axes.helpers import get_client_ip_address

    request = request_with(
        REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.7"
    )

    assert get_client_ip_address(request) == "203.0.113.7"


@override_settings(**BEHIND_NGINX)
def test_axes_no_longer_sees_every_visitor_as_the_proxy():
    """Two people on two continents used to share one lockout bucket.

    Because that bucket is keyed on address *and* username together, sharing
    it turned the lockout into one on the username: anybody could spend a
    stranger's five attempts and lock them out for half an hour.
    """
    from axes.helpers import get_client_ip_address

    one = request_with(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.7")
    another = request_with(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="198.51.100.4")

    assert get_client_ip_address(one) != get_client_ip_address(another)


# ---------------------------------------------------------------------------
# Through the API, end to end
# ---------------------------------------------------------------------------
LOGIN = "/api/v1/auth/login/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"
SPA = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
PASSWORD = "TestPass12345"


@pytest.fixture
def limited(settings, monkeypatch):
    """Sign-in limited to three attempts, counted for real.

    The suite runs with every rate set to None, so a test about a rate limit
    has to switch one on. Overriding the setting is not enough on its own:
    DRF copies the rates onto `SimpleRateThrottle.THROTTLE_RATES` when the
    module is first imported, and that copy is what every throttle instance
    reads. The same freeze-at-import trap the signing key and NUM_PROXIES had
    in the settings modules — here it has to be patched on the class.
    """
    from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle

    rates = {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "auth": "3/min"}
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_CLASSES": (ScopedRateThrottle,),
        "DEFAULT_THROTTLE_RATES": rates,
        "NUM_PROXIES": 1,
    }
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", rates)
    settings.TRUSTED_PROXY_COUNT = 1
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_one_client_cannot_rotate_the_header_past_the_sign_in_limit(api, student, limited):
    """Four attempts from one address, each claiming a different one."""
    codes = []
    for n in range(4):
        response = api.post(
            LOGIN,
            {"email": student.email, "password": "wrong-on-purpose"},
            format="json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR=f"10.0.0.{n}, 203.0.113.7",
        )
        codes.append(response.status_code)

    assert codes[-1] == 429, codes
    assert codes.count(429) == 1, codes  # the first three were allowed through


@pytest.mark.django_db
def test_two_different_clients_keep_their_own_allowance(api, student, limited):
    """The limit must be per person, not per proxy."""
    for _ in range(3):
        api.post(
            LOGIN,
            {"email": student.email, "password": "wrong-on-purpose"},
            format="json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.7",
        )

    somebody_else = api.post(
        LOGIN,
        {"email": student.email, "password": "wrong-on-purpose"},
        format="json",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="198.51.100.4",
    )

    assert somebody_else.status_code != 429


@pytest.mark.django_db
@override_settings(**BEHIND_NGINX)
def test_the_login_record_keeps_the_address_the_proxy_wrote(api, student):
    response = api.post(
        LOGIN,
        {"email": student.email, "password": PASSWORD},
        format="json",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.7",
    )

    assert response.status_code == 200, response.data
    student.refresh_from_db()
    assert student.last_login_ip == "203.0.113.7"


@pytest.mark.django_db
@override_settings(**BEHIND_NGINX)
def test_the_audit_trail_keeps_the_address_the_proxy_wrote(api, student):
    from apps.audit.models import AuditLog

    api.post(
        LOGIN,
        {"email": student.email, "password": PASSWORD},
        format="json",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.7",
    )

    recorded = {entry.ip for entry in AuditLog.objects.all() if entry.ip}
    assert recorded, "the sign-in left no audit entry to check"
    assert recorded == {"203.0.113.7"}
    assert "10.0.0.1" not in recorded


@pytest.mark.django_db
@override_settings(**BEHIND_NGINX)
def test_the_new_device_alert_quotes_the_address_the_proxy_wrote(api, student):
    """The address in the email is the one the account holder has to judge.

    Two sign-ins, because a first-ever device raises no alert by design —
    there is nothing to compare it against.
    """
    from apps.notifications.models import Notification, NotificationType

    chrome = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    )
    iphone = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 "
        "Safari/604.1"
    )
    credentials = {"email": student.email, "password": PASSWORD}

    api.post(LOGIN, credentials, format="json", HTTP_USER_AGENT=chrome)
    api.post(
        LOGIN,
        credentials,
        format="json",
        HTTP_USER_AGENT=iphone,
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="10.0.0.1, 203.0.113.7",
    )

    alert = Notification.objects.filter(
        user=student, type=NotificationType.NEW_DEVICE_LOGIN
    ).first()
    assert alert is not None, "signing in from a second device raised no alert"
    assert alert.payload["ip"] == "203.0.113.7"


@pytest.mark.django_db
@override_settings(**BEHIND_NGINX)
def test_signing_in_refreshing_and_out_still_work_behind_the_proxy(api, student):
    """The flow itself is untouched: same endpoints, same codes, same cookie."""
    proxied = {"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "203.0.113.7"}

    signed_in = api.post(
        LOGIN, {"email": student.email, "password": PASSWORD}, format="json", **proxied
    )
    assert signed_in.status_code == 200, signed_in.data
    assert signed_in.data["access"]

    refreshed = api.post(REFRESH, **SPA, **proxied)
    assert refreshed.status_code == 200, refreshed.data
    assert refreshed.data["access"]

    assert api.post(LOGOUT, **SPA, **proxied).status_code == 204
