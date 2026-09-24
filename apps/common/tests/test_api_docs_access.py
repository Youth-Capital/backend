"""The API's own map is for the people who run it.

`/api/v1/schema/` answered 200 to anyone, signed in or not: every route, every
parameter, every request shape — including the admin endpoints. That is not a
hole by itself, but it is the map a person uses to find one; the answer-key
endpoint in assessment is exactly the kind of route it lists.
"""

import pytest

pytestmark = pytest.mark.django_db

DOCS = ["/api/v1/schema/", "/api/v1/docs/", "/api/v1/redoc/"]


@pytest.mark.parametrize("url", DOCS)
def test_a_signed_out_visitor_cannot_read_the_api_map(api, url):
    assert api.get(url).status_code in {401, 403}


@pytest.mark.parametrize("url", DOCS)
def test_a_learner_cannot_either(auth, student, url):
    assert auth(student).get(url).status_code == 403


def test_an_employer_cannot_either(auth, employer):
    assert auth(employer).get("/api/v1/schema/").status_code == 403


def test_an_admin_still_can(auth, admin_user):
    response = auth(admin_user).get("/api/v1/schema/")

    assert response.status_code == 200
    assert b"/api/v1/" in response.content
