"""The safety queue an admin actually works through.

The monitoring panel already counted these events — "self_harm: 3" — which is
as much use as it sounds. A count cannot be followed up: it does not say who
it happened to, when, or whether anybody has read it. On a platform serving
minors that is the one queue somebody has to be able to clear, so it is a list
with names and an open/closed state.

Names are the point, and also the risk. This endpoint hands an admin the
identity of a learner who typed something about self-harm — necessary, because
following it up is why the layer records anything at all, and dangerous, which
is why the tests below check that nobody else can open it and that reading it
is written to the audit trail.
"""

import pytest

pytestmark = pytest.mark.django_db

EVENTS_URL = "/api/v1/ai/safety-events/"


@pytest.fixture
def events(db, student):
    from apps.ai.models import AISafetyEvent, SafetyAction, SafetySeverity

    blocked = AISafetyEvent.objects.create(
        user=student,
        rule="self_harm",
        action=SafetyAction.BLOCKED,
        severity=SafetySeverity.HIGH,
    )
    escalated = AISafetyEvent.objects.create(
        user=student,
        rule="medical",
        action=SafetyAction.ESCALATED,
        severity=SafetySeverity.MEDIUM,
    )
    return blocked, escalated


# -- who may open it -------------------------------------------------------
def test_an_admin_sees_the_queue(auth, admin_user, events):
    response = auth(admin_user).get(EVENTS_URL)

    assert response.status_code == 200, response.data
    assert response.data["count"] == 2


@pytest.mark.parametrize("who", ["student", "employer"])
def test_nobody_else_can_open_it(auth, request, who, events):
    """It names a learner who typed something about self-harm."""
    user = request.getfixturevalue(who)

    response = auth(user).get(EVENTS_URL)

    assert response.status_code in {403, 404}


def test_a_learner_cannot_read_their_own_events_either(auth, student, events):
    """Not a privacy feature for them — a queue for the people who respond."""
    response = auth(student).get(EVENTS_URL)

    assert response.status_code in {403, 404}


# -- what it has to say to be actionable -----------------------------------
def test_the_row_names_the_person(auth, admin_user, events, student):
    response = auth(admin_user).get(EVENTS_URL)

    row = response.data["results"][0]
    assert row["user_email"] == student.email
    assert row["rule"]
    assert row["severity"]
    assert row["action"]
    assert "is_minor" in row, "whether they are a minor changes the right response"


def test_reading_the_queue_is_audited(auth, admin_user, events):
    """Handing over a learner's identity is a personal-data access."""
    from apps.audit.models import AuditAction, AuditLog

    before = AuditLog.objects.filter(action=AuditAction.PII_ACCESS).count()

    auth(admin_user).get(EVENTS_URL)

    assert AuditLog.objects.filter(action=AuditAction.PII_ACCESS).count() == before + 1


# -- working through it ----------------------------------------------------
def test_an_event_can_be_closed_with_a_note(auth, admin_user, events):
    blocked, _ = events

    response = auth(admin_user).post(
        f"{EVENTS_URL}{blocked.id}/review/",
        {"note": "Связались с куратором, ученик направлен к психологу."},
        format="json",
    )

    assert response.status_code == 200, response.data
    blocked.refresh_from_db()
    assert blocked.reviewed_at is not None
    assert blocked.reviewed_by_id == admin_user.id
    assert "психологу" in blocked.review_note


def test_open_and_closed_can_be_told_apart(auth, admin_user, events):
    """Without this the list only grows and stops being read."""
    blocked, _ = events
    client = auth(admin_user)
    client.post(f"{EVENTS_URL}{blocked.id}/review/", {"note": "done"}, format="json")

    still_open = client.get(f"{EVENTS_URL}?state=open")
    closed = client.get(f"{EVENTS_URL}?state=closed")

    assert still_open.data["count"] == 1
    assert closed.data["count"] == 1


def test_closing_one_by_mistake_can_be_undone(auth, admin_user, events):
    blocked, _ = events
    client = auth(admin_user)
    client.post(f"{EVENTS_URL}{blocked.id}/review/", {"note": "oops"}, format="json")

    client.post(f"{EVENTS_URL}{blocked.id}/reopen/")

    blocked.refresh_from_db()
    assert blocked.reviewed_at is None
    assert blocked.reviewed_by_id is None


def test_the_summary_counts_what_is_still_open(auth, admin_user, events):
    blocked, _ = events
    client = auth(admin_user)

    before = client.get(f"{EVENTS_URL}summary/").data
    assert before["open"] == 2
    assert before["open_high"] == 1

    client.post(f"{EVENTS_URL}{blocked.id}/review/", {"note": "done"}, format="json")

    after = client.get(f"{EVENTS_URL}summary/").data
    assert after["open"] == 1
    assert after["open_high"] == 0


def test_a_student_cannot_close_an_event(auth, student, events):
    blocked, _ = events

    response = auth(student).post(
        f"{EVENTS_URL}{blocked.id}/review/", {"note": "nothing to see"}, format="json"
    )

    assert response.status_code in {403, 404}
    blocked.refresh_from_db()
    assert blocked.reviewed_at is None


# -- the layer actually fills this queue -----------------------------------
def test_a_blocked_message_lands_in_the_queue(auth, admin_user, student):
    """End to end: the screen refuses something, and it appears here."""
    from apps.ai.safety import check_text

    check_text("убей себя", user=student)

    response = auth(admin_user).get(f"{EVENTS_URL}?state=open")

    rules = [row["rule"] for row in response.data["results"]]
    assert "self_harm" in rules
