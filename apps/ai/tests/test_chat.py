"""The grounded chat.

Two properties matter more than anything else here, and both are load-bearing
for the product rather than for the chat:

* the assistant reports the numbers the engine actually stored, so an
  explanation can be checked against the screen it explains;
* it cannot be talked into revealing something the caller is not entitled to.
"""

import pytest
from rest_framework.test import APIClient

from apps.ai.chat import Intent, answer, classify
from apps.ai.models import ChatMessage
from apps.common.enums import ModerationStatus, Role
from apps.profiles.models import EmployerProfile, StudentProfile
from apps.taxonomy.models import Skill, SkillCategory

pytestmark = pytest.mark.django_db


@pytest.fixture
def student(django_user_model):
    user = django_user_model.objects.create_user(
        email="learner@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    StudentProfile.objects.create(user=user, youth_id="YK-CHAT-0001")
    return user


@pytest.fixture
def employer(django_user_model):
    user = django_user_model.objects.create_user(
        email="hr@example.com", password="Str0ng!passw0rd", role=Role.EMPLOYER
    )
    EmployerProfile.objects.create(owner=user, legal_name="Acme", slug="acme")
    return user


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# -- classification ---------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Почему у меня такой процент совпадения?", Intent.MATCH_EXPLAIN),
        ("как считается балл?", Intent.MATCH_METHOD),
        ("чего мне не хватает до аналитика", Intent.GAP),
        ("что дальше делать", Intent.NEXT_STEP),
        ("откуда взялся уровень навыка", Intent.SKILL_EXPLAIN),
        ("как считается индекс капитала", Intent.CAPITAL_EXPLAIN),
        ("что ты умеешь", Intent.HELP),
    ],
)
def test_student_questions_are_classified(student, text, expected):
    assert classify(text, Role.STUDENT) == expected


def test_longest_keyword_wins(student):
    """"как считается" must beat the bare "счита" inside it."""
    assert classify("как считается мой балл", Role.STUDENT) == Intent.MATCH_METHOD


def test_roles_reach_different_intents(student, employer):
    # A student asking about candidates is asking about their own standing,
    # so the employer-only intent must not be reachable for them.
    assert classify("почему этот кандидат", Role.STUDENT) != Intent.CANDIDATE_EXPLAIN
    assert classify("почему этот кандидат", Role.EMPLOYER) == Intent.CANDIDATE_EXPLAIN


# -- grounding --------------------------------------------------------------
def test_match_answer_reports_the_stored_numbers(student, django_user_model):
    from apps.jobs.models import Vacancy
    from apps.matching.models import MatchResult

    company = EmployerProfile.objects.create(
        owner=django_user_model.objects.create_user(
            email="co@example.com", password="Str0ng!passw0rd", role=Role.EMPLOYER
        ),
        legal_name="Nexora",
        slug="nexora",
    )
    vacancy = Vacancy.objects.create(
        employer=company, title="Junior Analyst", status=ModerationStatus.PUBLISHED
    )
    MatchResult.objects.create(
        student=student,
        vacancy=vacancy,
        overall_score=87,
        # The engine stores component scores in columns; `breakdown` holds the
        # weights and diagnostics. An earlier version of this test invented a
        # breakdown shape, so it passed while the screen showed "skills_met 3%".
        coverage_score=92,
        knowledge_score=85,
        verification_score=90,
        experience_score=60,
        education_score=100,
        location_score=40,
        breakdown={"weights": {"coverage": 0.35}, "skills_required": 4, "skills_met": 3},
        explanation=[{"code": "verified_skills", "sentiment": "positive", "data": {}}],
    )

    result = answer(student, "почему такой процент?")

    # Not a plausible-sounding number — the one on the row.
    assert result.facts["overall"] == 87
    assert result.facts["components"]["coverage"] == 92
    assert result.facts["components"]["location"] == 40
    # Counts must not sit in the components table pretending to be percentages.
    assert "skills_met" not in result.facts["components"]
    assert result.facts["vacancy"] == "Junior Analyst"
    assert result.sources[0]["type"] == "MatchResult"


def test_method_answer_quotes_the_live_weights(student):
    from apps.matching.models import MatchWeightProfile

    result = answer(student, "как считается балл?")

    # Quoting the weights actually in use means this answer cannot drift out
    # of date when somebody retunes the engine through the admin.
    assert result.facts["weights"] == MatchWeightProfile.active().normalised_weights()
    assert sum(result.facts["weights"].values()) == pytest.approx(1.0)
    assert result.facts["evidence_weights"]["SELF"] == 0.35
    assert result.facts["evidence_weights"]["TEST"] == 0.90


def test_skill_answer_lists_the_evidence_behind_each_level(student):
    from apps.common.enums import EvidenceSource
    from apps.profiles.services import record_skill_evidence

    category = SkillCategory.objects.create(name_uz="Data", slug="data")
    skill = Skill.objects.create(name_uz="SQL", slug="sql", category=category)
    record_skill_evidence(
        user=student, skill=skill, source=EvidenceSource.SELF, score=60
    )

    result = answer(student, "откуда уровень навыка?")

    row = result.facts["skills"][0]
    assert row["skill"] == "SQL"
    assert row["evidence"][0]["source"] == EvidenceSource.SELF
    assert row["evidence"][0]["weight"] == 0.35


def test_empty_state_says_so_instead_of_inventing(student):
    result = answer(student, "почему такой процент?")

    assert result.code == "match.none"
    assert result.facts == {}


# -- privacy ----------------------------------------------------------------
def test_candidate_answer_never_returns_a_name(employer, django_user_model):
    """Identity is revealed only after a candidate applies (TZ §12).

    The chat must not become a way around that rule.
    """
    from apps.jobs.models import Vacancy
    from apps.matching.models import MatchResult

    learner = django_user_model.objects.create_user(
        email="hidden@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    StudentProfile.objects.create(
        user=learner, youth_id="YK-CHAT-0002", first_name="Aziza", last_name="Yusupova"
    )
    vacancy = Vacancy.objects.create(
        employer=employer.employer_profile,
        title="Analyst",
        status=ModerationStatus.PUBLISHED,
    )
    MatchResult.objects.create(
        student=learner, vacancy=vacancy, overall_score=91, breakdown={}
    )

    result = answer(employer, "почему этот кандидат подходит?")

    blob = str(result.facts)
    assert "Aziza" not in blob
    assert "Yusupova" not in blob
    assert result.facts["overall"] == 91


def test_employer_cannot_reach_a_learners_own_intents(employer):
    assert classify("чего мне не хватает", Role.EMPLOYER) != Intent.GAP


# -- endpoint ---------------------------------------------------------------
def test_chat_endpoint_stores_both_turns(student):
    response = _client(student).post(
        "/api/v1/ai/chat/", {"text": "что ты умеешь?"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["message"]["intent"] == Intent.HELP
    assert ChatMessage.objects.filter(user=student).count() == 2


def test_empty_message_is_refused(student):
    response = _client(student).post("/api/v1/ai/chat/", {"text": "   "}, format="json")
    assert response.status_code == 400


def test_first_question_names_the_conversation(student):
    """The title is what the person typed, so they recognise it in the list."""
    client = _client(student)
    client.post("/api/v1/ai/chat/", {"text": "почему такой процент?"}, format="json")
    client.post("/api/v1/ai/chat/", {"text": "а что дальше?"}, format="json")

    threads = client.get("/api/v1/ai/chat/").json()["threads"]

    assert len(threads) == 1
    assert threads[0]["title"] == "почему такой процент?"


def test_a_new_conversation_receives_the_next_message(student):
    """Regression: a fresh thread sorted last, so the next question — sent
    without naming a thread — landed in the previous conversation."""
    client = _client(student)
    client.post("/api/v1/ai/chat/", {"text": "почему такой процент?"}, format="json")
    client.post("/api/v1/ai/chat/", {"action": "new"}, format="json")

    client.post("/api/v1/ai/chat/", {"text": "как считается балл?"}, format="json")

    threads = client.get("/api/v1/ai/chat/").json()["threads"]
    assert threads[0]["title"] == "как считается балл?"
    # And the older conversation kept exactly its own two turns.
    older = client.get(f"/api/v1/ai/chat/?thread={threads[1]['id']}").json()
    assert len(older["messages"]) == 2


def test_new_conversation_starts_empty_and_keeps_the_old_one(student):
    client = _client(student)
    client.post("/api/v1/ai/chat/", {"text": "помощь"}, format="json")

    started = client.post("/api/v1/ai/chat/", {"action": "new"}, format="json").json()

    assert started["messages"] == []
    assert len(started["threads"]) == 2
    # The earlier conversation is still readable, not replaced.
    old = [t for t in started["threads"] if t["title"]][0]
    body = client.get(f"/api/v1/ai/chat/?thread={old['id']}").json()
    assert len(body["messages"]) == 2


def test_one_conversation_can_be_deleted_without_the_rest(student):
    from apps.ai.models import ChatThread

    client = _client(student)
    client.post("/api/v1/ai/chat/", {"text": "помощь"}, format="json")
    client.post("/api/v1/ai/chat/", {"action": "new"}, format="json")
    client.post("/api/v1/ai/chat/", {"text": "что дальше"}, format="json")

    first = ChatThread.objects.filter(user=student).order_by("created_at").first()
    assert client.delete(f"/api/v1/ai/chat/?thread={first.id}").status_code == 204

    assert ChatThread.objects.filter(user=student).count() == 1
    assert not ChatMessage.objects.filter(thread=first).exists()


def test_another_users_conversation_cannot_be_read(student, django_user_model):
    from apps.ai.models import ChatThread

    stranger = django_user_model.objects.create_user(
        email="stranger@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    StudentProfile.objects.create(user=stranger, youth_id="YK-CHAT-0004")
    _client(student).post("/api/v1/ai/chat/", {"text": "помощь"}, format="json")
    mine = ChatThread.objects.get(user=student)

    response = _client(stranger).get(f"/api/v1/ai/chat/?thread={mine.id}")

    assert response.status_code == 400


def test_history_is_scoped_to_the_caller(student, django_user_model):
    other = django_user_model.objects.create_user(
        email="other@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    StudentProfile.objects.create(user=other, youth_id="YK-CHAT-0003")

    _client(student).post("/api/v1/ai/chat/", {"text": "помощь"}, format="json")

    response = _client(other).get("/api/v1/ai/chat/")

    assert response.status_code == 200
    assert response.json()["messages"] == []


def test_every_conversation_can_be_cleared_at_once(student):
    from apps.ai.models import ChatThread

    _client(student).post("/api/v1/ai/chat/", {"text": "помощь"}, format="json")

    assert _client(student).delete("/api/v1/ai/chat/").status_code == 204
    assert not ChatThread.objects.filter(user=student).exists()
    assert not ChatMessage.objects.filter(user=student).exists()


# -- storage safety ---------------------------------------------------------
def test_every_handler_produces_storable_facts(student, employer):
    """Grounding is persisted, so a value JSON cannot hold fails the whole reply.

    Regression guard: `UserSkill.confidence` is a Decimal, which JSONField
    refuses, and the chat answered with a 500 instead of an explanation. Model
    columns here are Decimals, dates and UUIDs all over, so this checks every
    handler rather than the one field that broke.
    """
    import json

    from apps.ai.chat import HANDLERS, ROLE_INTENTS

    for user in (student, employer):
        for intent in ROLE_INTENTS[user.role]:
            result = HANDLERS[intent](user)
            from apps.ai.chat import jsonable

            try:
                json.dumps(jsonable(result.facts))
                json.dumps(jsonable(result.sources))
            except TypeError as error:  # pragma: no cover - the failure message matters
                pytest.fail(f"{user.role}/{intent} produced unstorable facts: {error}")


def test_skill_answer_survives_decimal_columns(student):
    from apps.common.enums import EvidenceSource
    from apps.profiles.services import record_skill_evidence

    category = SkillCategory.objects.create(name_uz="Data", slug="data-2")
    skill = Skill.objects.create(name_uz="Excel", slug="excel", category=category)
    record_skill_evidence(
        user=student, skill=skill, source=EvidenceSource.SELF, score=70
    )

    response = _client(student).post(
        "/api/v1/ai/chat/", {"text": "откуда уровень навыка?"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["message"]["code"] == "skill.explain"


def test_capital_answer_counts_axes_correctly(student):
    """"Index 39, 0 of 0 axes measured" was a sentence contradicting itself.

    The service reports `measured_axes` / `total_axes`; the answer was reading
    a key called `axes` that does not exist and getting an empty list.
    """
    from apps.ai.chat import _capital_explain
    from apps.capital.services import get_capital_overview
    from apps.taxonomy.models import CapitalDimension, CapitalDimensionSlug

    for order, slug in enumerate(list(CapitalDimensionSlug.values)[:3]):
        CapitalDimension.objects.create(
            slug=slug, name_uz=slug.title(), order=order
        )

    overview = get_capital_overview(student)
    result = _capital_explain(student)

    assert result.facts["total"] == overview["total_axes"] == 3
    assert result.facts["measured"] == overview["measured_axes"]


# -- open questions ---------------------------------------------------------
def test_unrecognised_question_gets_a_briefing_not_a_shrug(student):
    """The reply to "с чего начать" must not be "pick from the list".

    An unplaceable question is answered with where the person stands, which
    happens to answer most open questions on its own.
    """
    # A bare greeting — the most common input that matches no keyword at all.
    # ("с чего мне начать" is now understood, and routes to NEXT_STEP.)
    result = answer(student, "привет!")

    assert result.code == "briefing.student"
    assert "skills_total" in result.facts
    assert "capital" in result.facts


def test_employers_get_their_own_briefing(employer):
    result = answer(employer, "ну и что там у меня")

    assert result.code == "briefing.employer"
    assert "applications" in result.facts


@pytest.mark.parametrize(
    "text,expected",
    [
        # Inserted words used to break substring matching outright.
        ("С чего мне стоит начать", Intent.NEXT_STEP),
        ("что мне дальше-то делать", Intent.NEXT_STEP),
        ("а почему у меня именно такой процент совпадения", Intent.MATCH_EXPLAIN),
        ("откуда вообще взялся мой уровень навыка", Intent.SKILL_EXPLAIN),
        ("как вы считаете индекс капитала", Intent.CAPITAL_EXPLAIN),
    ],
)
def test_natural_phrasing_is_understood(student, text, expected):
    assert classify(text, Role.STUDENT) == expected


def test_model_failure_leaves_the_grounded_answer_intact(student, monkeypatch):
    """A backend outage costs fluency, never the answer."""
    from apps.ai.api import views

    monkeypatch.setattr(views, "_phrase_with_model", lambda *a, **k: "")

    response = _client(student).post(
        "/api/v1/ai/chat/", {"text": "что ты умеешь?"}, format="json"
    )

    body = response.json()["message"]
    assert response.status_code == 200
    # The rule-based code is still there, so the client renders a real sentence.
    assert body["code"] == "help.list"
    assert body["text"] == ""


def test_catalogue_questions_are_answered(student):
    """"What can I study here" is a basic question that had no handler."""
    from apps.taxonomy.models import Profession

    Profession.objects.create(name_uz="Data Analyst", slug="da")

    result = answer(student, "какие профессии можно изучать на этом сайте")

    assert result.code == "professions.list"
    assert result.facts["count"] == 1
    assert result.facts["professions"][0]["name"] == "Data Analyst"


def test_course_catalogue_is_answered(student):
    assert classify("какие курсы у вас есть", Role.STUDENT) == Intent.COURSES
    assert classify("чему можно научиться", Role.STUDENT) == Intent.COURSES


def test_employers_can_also_browse_the_catalogue(employer):
    """They hire against these professions, so the list is theirs to read too."""
    assert classify("какие профессии есть", Role.EMPLOYER) == Intent.PROFESSIONS


def test_briefing_tasks_use_the_same_shape_as_next_step(student):
    """One field name, one shape.

    The briefing sent bare strings while `next.tasks` sent objects, so the
    renderer printed "1." and "2." with nothing beside them.
    """
    plain = answer(student, "привет!").facts["tasks"]
    assert all(isinstance(row, dict) and "title" in row for row in plain)


# -- the hosted model path --------------------------------------------------
def test_a_configured_model_answers_freely_and_the_facts_still_ground_it(
    student, monkeypatch
):
    """The open-question path: rules classify, the model phrases.

    Proven with a stub rather than a live call so the test is deterministic and
    costs nothing — what it verifies is the wiring: the model's text reaches
    the stored message, and the grounded facts travel with it.
    """
    from apps.ai.api import views

    captured = {}

    def fake_phrase(user, thread, question, facts):
        captured["question"] = question
        captured["facts"] = facts
        return "For a hackathon, start by picking a problem you understand."

    monkeypatch.setattr(views, "_phrase_with_model", fake_phrase)

    response = _client(student).post(
        "/api/v1/ai/chat/", {"text": "дай мне совет по хакатону"}, format="json"
    )

    body = response.json()["message"]
    assert response.status_code == 200
    # The model's own wording is what the reader sees...
    assert body["text"].startswith("For a hackathon")
    # ...and it was handed the person's real data to ground it.
    assert "skills_total" in captured["facts"]
    assert captured["question"] == "дай мне совет по хакатону"


def test_without_a_model_the_answer_is_honest_about_its_limit(student):
    """No key configured: the briefing must not pose as an answer."""
    response = _client(student).post(
        "/api/v1/ai/chat/", {"text": "дай мне совет по хакатону"}, format="json"
    )

    body = response.json()["message"]
    assert body["code"] == "briefing.student"
    # No invented free text — the client renders the honest template instead.
    assert body["text"] == ""


# -- the language the model is told to write in -----------------------------
#
# The product is trilingual, and the model has no way of knowing which of the
# three the reader chose: `Accept-Language` sets Django's active locale and the
# backend turns that into an instruction in the system prompt. If that chain
# breaks anywhere the model does not fail — it silently answers everyone in the
# default language, which is a bug nobody notices until a Russian-speaking user
# gets Uzbek back.
#
# Stubbed rather than called for real: what is being pinned is the wiring, and
# a live call would be slow, paid and non-deterministic.
@pytest.fixture
def anthropic_backend(monkeypatch):
    """An active Anthropic provider whose SDK is replaced by a recorder."""
    import sys
    import types

    from apps.ai.models import AIProvider, AIProviderConfig

    AIProviderConfig.objects.create(
        name="test", provider=AIProvider.ANTHROPIC, model="claude-opus-5",
        api_key_env_name="TEST_AI_KEY", is_active=True,
    )
    monkeypatch.setenv("TEST_AI_KEY", "not-a-real-key")

    seen = {}

    class Block:
        type = "text"
        text = "Answer."

    class Response:
        stop_reason = "end_turn"
        content = [Block()]

    class Messages:
        def create(self, **kwargs):
            seen.update(kwargs)
            return Response()

    class Anthropic:
        def __init__(self, **_):
            self.messages = Messages()

    monkeypatch.setitem(
        sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic)
    )
    return seen


@pytest.mark.parametrize(
    "header,expected",
    [("ru", "Russian"), ("en", "English"), ("uz", "Uzbek (latin script)")],
)
def test_the_model_is_told_to_answer_in_the_readers_language(
    student, anthropic_backend, header, expected
):
    client = _client(student)
    response = client.post(
        "/api/v1/ai/chat/",
        {"text": "дай мне совет по хакатону"},
        format="json",
        headers={"accept-language": header},
    )

    assert response.status_code == 200, response.data
    assert anthropic_backend, "the model was never called"
    # The instruction itself, not the bare language name: the prompt already
    # contains the word "Uzbek" inside "Uzbekistan", so a substring check on
    # the name alone would pass even with the wiring torn out.
    system = anthropic_backend["system"]
    assert f"Write in {expected}." in system, (
        f"Accept-Language: {header} should ask for {expected}, but the prompt "
        f"says: {system.splitlines()[-1]}"
    )


def test_an_unknown_language_falls_back_instead_of_breaking(
    student, anthropic_backend
):
    """A header naming a language the platform does not have must still answer."""
    response = _client(student).post(
        "/api/v1/ai/chat/",
        {"text": "дай мне совет по хакатону"},
        format="json",
        headers={"accept-language": "de"},
    )

    assert response.status_code == 200, response.data
    assert "Write in Uzbek (latin script)." in anthropic_backend["system"]
