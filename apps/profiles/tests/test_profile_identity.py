"""The identity half of the profile page: names, patronymic, phone.

Three things are worth pinning here, each a way the page could quietly go
wrong for the person using it:

  1. The patronymic has to be saved and read back. It is a new column, and a
     field the form shows but the serializer drops is a field that looks saved
     until the page is reloaded.
  2. It has to stay optional. Not everybody has one, and a form that refuses to
     save without it is a form people abandon.
  3. The phone goes through the same validator and uniqueness rule as sign-up.
     The profile page is a second way to set it, and a second way is exactly
     where a rule gets forgotten.
"""

import pytest

pytestmark = pytest.mark.django_db

PROFILE_URL = "/api/v1/me/profile/"
ME_URL = "/api/v1/auth/me/"


def test_the_patronymic_is_saved_and_read_back(auth, student):
    client = auth(student)

    response = client.patch(
        PROFILE_URL,
        {"last_name": "Yusupova", "first_name": "Aziza", "middle_name": "Rustamovna"},
        format="json",
    )
    assert response.status_code == 200, response.data

    fresh = client.get(PROFILE_URL)
    assert fresh.data["middle_name"] == "Rustamovna"
    assert fresh.data["last_name"] == "Yusupova"


def test_the_patronymic_is_optional(auth, student):
    """Saving the other names must not require it, and must not invent one."""
    client = auth(student)

    response = client.patch(PROFILE_URL, {"first_name": "Aziza"}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["middle_name"] == ""


def test_one_learner_cannot_rename_another(auth, student, other_student):
    """/me/profile/ is always the caller's own row, whatever is sent."""
    before = other_student.student_profile.middle_name

    auth(student).patch(PROFILE_URL, {"middle_name": "Changed"}, format="json")

    other_student.student_profile.refresh_from_db()
    assert other_student.student_profile.middle_name == before


@pytest.mark.parametrize("bad", ["12ab", "+99", "not a phone"])
def test_a_malformed_phone_is_refused(auth, student, bad):
    response = auth(student).patch(ME_URL, {"phone": bad}, format="json")

    assert response.status_code == 400, response.data
    student.refresh_from_db()
    assert student.phone != bad


def test_a_valid_phone_is_saved(auth, student):
    response = auth(student).patch(ME_URL, {"phone": "+998901234567"}, format="json")

    assert response.status_code == 200, response.data
    student.refresh_from_db()
    assert student.phone == "+998901234567"


def test_a_phone_already_in_use_is_refused(auth, student, other_student):
    other_student.phone = "+998901112233"
    other_student.save(update_fields=["phone"])

    response = auth(student).patch(ME_URL, {"phone": "+998901112233"}, format="json")

    assert response.status_code == 400, response.data


def test_clearing_the_phone_stores_null_not_an_empty_string(
    auth, student, other_student
):
    """Two accounts that both cleared their number must not collide on ""."""
    other_student.phone = None
    other_student.save(update_fields=["phone"])
    student.phone = "+998907778899"
    student.save(update_fields=["phone"])

    response = auth(student).patch(ME_URL, {"phone": None}, format="json")

    assert response.status_code == 200, response.data
    student.refresh_from_db()
    assert student.phone is None
