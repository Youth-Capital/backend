"""No account without the three required consents — enforced by the server.

The registration form now starts with every consent unticked and keeps its
button disabled until the required box is checked. That is a convenience. The
guarantee is here: `register_user` refuses to create an account when any of
TERMS, PRIVACY or DATA_PROCESSING is missing, so a request that skips the form
— or a form with the button re-enabled in dev tools — still gets nothing.
"""

import pytest

from apps.accounts.models import Consent, ConsentType, User

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/register/"


def _payload(**overrides):
    payload = {
        "email": "new.learner@example.com",
        "password": "a-long-enough-passphrase-42",
        "role": "STUDENT",
        "preferred_language": "ru",
        "first_name": "Aziza",
        "last_name": "Yusupova",
        "birth_date": "2004-05-10",
        "consents": ["TERMS", "PRIVACY", "DATA_PROCESSING"],
    }
    payload.update(overrides)
    return payload


def test_all_three_required_consents_create_the_account(api):
    response = api.post(URL, _payload(), format="json")

    assert response.status_code == 201, response.data
    user = User.objects.get(email="new.learner@example.com")
    recorded = set(Consent.objects.filter(user=user).values_list("type", flat=True))
    assert {ConsentType.TERMS, ConsentType.PRIVACY, ConsentType.DATA_PROCESSING} <= recorded


@pytest.mark.parametrize(
    "consents",
    [
        [],
        ["TERMS", "PRIVACY"],
        ["PRIVACY", "DATA_PROCESSING"],
        ["TERMS", "DATA_PROCESSING"],
        # Optional consents do not stand in for a required one.
        ["AI_PROCESSING", "TALENT_SEARCH"],
    ],
)
def test_a_missing_required_consent_creates_nothing(api, consents):
    response = api.post(URL, _payload(consents=consents), format="json")

    assert 400 <= response.status_code < 500, response.data
    assert not User.objects.filter(email="new.learner@example.com").exists()


def test_optional_consents_are_recorded_only_when_given(api):
    response = api.post(
        URL,
        _payload(consents=["TERMS", "PRIVACY", "DATA_PROCESSING", "AI_PROCESSING"]),
        format="json",
    )

    assert response.status_code == 201, response.data
    user = User.objects.get(email="new.learner@example.com")
    recorded = set(Consent.objects.filter(user=user).values_list("type", flat=True))
    assert ConsentType.AI_PROCESSING in recorded
    assert ConsentType.TALENT_SEARCH not in recorded
