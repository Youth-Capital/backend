"""Recommending only the kind of work somebody is actually after.

The complaint this answers: a learner heading for data analytics, who has
Python and SQL, was shown every DevOps and security vacancy that also wanted
Python or SQL. The scores were right. The list was useless, because skill
overlap is not the same question as "is this the job I want".

Two things are being protected, and the second is the one that would ruin the
product quietly:

  * relevance filters the feed;
  * it never touches the match score, and it never empties a feed for somebody
    who has told the platform nothing to filter on.
"""

import pytest

from apps.common.enums import ModerationStatus, RequirementLevel
from apps.matching import relevance
from apps.matching.services import (
    candidate_pool_for_vacancy,
    vacancy_pool_for_student,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(db, taxonomy, employer):
    """Two professions that genuinely share a skill, which is the whole problem.

    Data analytics and DevOps both want Python. Under skill overlap alone,
    every DevOps vacancy reached every analytics learner.
    """
    from apps.jobs.models import Vacancy, VacancySkill
    from apps.taxonomy.models import Profession, ProfessionSkill, Skill, SkillCategory

    analyst = taxonomy["profession"]  # data-analyst, wants sql + power bi + python

    ops_category = SkillCategory.objects.create(slug="ops", name_uz="Ops")
    docker = Skill.objects.create(
        slug="docker", name_uz="Docker", category=ops_category
    )
    kubernetes = Skill.objects.create(
        slug="k8s", name_uz="Kubernetes", category=ops_category
    )

    devops = Profession.objects.create(slug="devops", name_uz="DevOps")
    for skill in (docker, kubernetes, taxonomy["python"]):
        ProfessionSkill.objects.create(
            profession=devops,
            skill=skill,
            requirement=RequirementLevel.REQUIRED,
            min_proficiency=50,
        )

    def vacancy(title, profession, skills):
        row = Vacancy.objects.create(
            employer=employer.employer_profile,
            title=title,
            description="…",
            profession=profession,
            region=taxonomy["region"],
            status=ModerationStatus.PUBLISHED,
        )
        for order, skill in enumerate(skills):
            VacancySkill.objects.create(
                vacancy=row,
                skill=skill,
                requirement=RequirementLevel.REQUIRED,
                min_knowledge_score=50,
                order=order,
            )
        return row

    return {
        "analyst": analyst,
        "devops": devops,
        "analyst_job": vacancy(
            "Junior Data Analyst", analyst, [taxonomy["sql"], taxonomy["power_bi"]]
        ),
        # The one that used to leak through: shares Python and nothing else.
        "devops_job": vacancy(
            "DevOps Engineer", devops, [docker, kubernetes, taxonomy["python"]]
        ),
        "ops_category": ops_category,
    }


def aim(student, profession=None, interests=()):
    profile = student.student_profile
    profile.target_profession = profession
    profile.save(update_fields=["target_profession"])
    profile.interests.set(interests)
    return student


# -- the named profession is the strongest signal --------------------------
def test_the_profession_the_learner_named_scores_full(student, world):
    aim(student, world["analyst"])

    result = relevance.for_student(student, world["analyst_job"])

    assert result.score == relevance.TARGET_PROFESSION_SCORE
    assert result.relevant
    assert result.reasons[0]["code"] == "target_profession"


def test_a_job_in_another_field_sharing_one_skill_is_not_relevant(student, world):
    """The complaint, as a test. Python is not a reason to offer somebody a
    DevOps job when they said they want analytics."""
    aim(student, world["analyst"])

    result = relevance.for_student(student, world["devops_job"])

    assert not result.relevant
    assert result.score < relevance.RELEVANT_ENOUGH


def test_a_neighbouring_job_with_a_different_title_still_reaches_them(
    student, world, taxonomy
):
    """A title match alone would throw this away, and it is the most useful
    recommendation the platform can make: the same work under another name."""
    from apps.jobs.models import Vacancy, VacancySkill
    from apps.taxonomy.models import Profession

    bi = Profession.objects.create(slug="bi-specialist", name_uz="BI mutaxassisi")
    job = Vacancy.objects.create(
        employer=world["analyst_job"].employer,
        title="BI Specialist",
        description="…",
        profession=bi,
        region=taxonomy["region"],
        status=ModerationStatus.PUBLISHED,
    )
    for order, skill in enumerate((taxonomy["sql"], taxonomy["power_bi"])):
        VacancySkill.objects.create(
            vacancy=job, skill=skill, requirement=RequirementLevel.REQUIRED,
            min_knowledge_score=50, order=order,
        )

    aim(student, world["analyst"])
    result = relevance.for_student(student, job)

    assert result.relevant
    assert result.reasons[0]["code"] == "shares_target_skills"


def test_a_ticked_interest_is_enough_on_its_own(student, world, taxonomy):
    """Somebody who has not picked a target profession but ticked a category
    still gets a filtered feed rather than an unfiltered one."""
    aim(student, None, interests=[world["ops_category"]])

    assert relevance.for_student(student, world["devops_job"]).relevant
    assert not relevance.for_student(student, world["analyst_job"]).relevant


# -- the cold start: silence is not a rejection ----------------------------
def test_a_learner_who_declared_nothing_is_never_filtered(student, world):
    """Filtering on a signal that does not exist would hand them an empty page,
    which is a worse product than the noisy one being fixed."""
    aim(student, None, interests=[])

    for job in (world["analyst_job"], world["devops_job"]):
        result = relevance.for_student(student, job)
        assert result.known is False
        assert result.relevant is True
        assert result.reasons[0]["code"] == "no_interests_declared"


def test_the_pool_falls_back_to_skills_when_nothing_is_declared(
    student, world, taxonomy
):
    from apps.profiles.models import UserSkill

    UserSkill.objects.create(user=student, skill=taxonomy["python"], proficiency=60)
    aim(student, None, interests=[])

    pool = list(vacancy_pool_for_student(student))

    assert world["devops_job"] in pool


# -- the pools -------------------------------------------------------------
def test_the_students_feed_drops_the_unrelated_job(student, world, taxonomy):
    from apps.profiles.models import UserSkill

    # Python and SQL: overlaps both vacancies, which is what made the old pool
    # return both.
    UserSkill.objects.create(user=student, skill=taxonomy["python"], proficiency=60)
    UserSkill.objects.create(user=student, skill=taxonomy["sql"], proficiency=60)
    aim(student, world["analyst"])

    pool = list(vacancy_pool_for_student(student))

    assert world["analyst_job"] in pool
    assert world["devops_job"] not in pool


def test_the_employers_shortlist_drops_a_candidate_aiming_elsewhere(
    student, world, taxonomy
):
    """Somebody whose stated direction is elsewhere is somebody likely to
    decline, and that wastes an interview rather than a few CPU cycles."""
    from apps.profiles.models import UserSkill

    UserSkill.objects.create(user=student, skill=taxonomy["python"], proficiency=70)
    aim(student, world["analyst"])

    pool = candidate_pool_for_vacancy(world["devops_job"])

    assert student not in pool


def test_the_employers_shortlist_keeps_a_candidate_who_wants_the_job(
    student, world, taxonomy
):
    from apps.profiles.models import UserSkill

    UserSkill.objects.create(user=student, skill=taxonomy["sql"], proficiency=70)
    aim(student, world["analyst"])

    assert student in candidate_pool_for_vacancy(world["analyst_job"])


# -- relevance must not move the score ------------------------------------
def test_relevance_is_stored_beside_the_score_and_not_inside_it(
    student, world, taxonomy
):
    """The number employers read has to mean what it meant before."""
    from apps.matching.engine import compute_match
    from apps.profiles.models import UserSkill

    UserSkill.objects.create(user=student, skill=taxonomy["python"], proficiency=80)

    aim(student, world["devops"])
    wanted = compute_match(student, world["devops_job"])

    aim(student, world["analyst"])
    unwanted = compute_match(student, world["devops_job"])

    # Same person, same vacancy, same skills — only the stated aim changed.
    assert wanted.overall == unwanted.overall
    assert wanted.coverage == unwanted.coverage
    # And the thing that did change is the thing that should have.
    assert wanted.relevance > unwanted.relevance


def test_the_stored_result_carries_the_reason(student, world, taxonomy):
    from apps.matching.engine import compute_and_store_match
    from apps.profiles.models import UserSkill

    UserSkill.objects.create(user=student, skill=taxonomy["sql"], proficiency=80)
    aim(student, world["analyst"])

    result = compute_and_store_match(student, world["analyst_job"])

    assert result.relevance_score == 100
    assert result.relevance_known is True
    assert result.relevance_reasons[0]["code"] == "target_profession"


# -- the feed endpoint -----------------------------------------------------
#
# The filter is applied in two places and that is not redundant: the pool
# decides what gets computed from now on, the endpoint decides what gets
# shown. Every row stored before relevance existed lives on until a full
# recompute reaches it, and without the second filter the feed stays noisy
# until then.
RECOMMENDATIONS = "/api/v1/matching/my/"


def test_a_stored_but_unwanted_match_does_not_reach_the_feed(
    auth, student, world, taxonomy
):
    from apps.matching.models import MatchResult

    aim(student, world["analyst"])
    # Written the way a pre-relevance row looks: a real score, no relevance.
    MatchResult.objects.create(
        student=student, vacancy=world["devops_job"], overall_score=88,
        relevance_score=10, relevance_known=True,
    )
    MatchResult.objects.create(
        student=student, vacancy=world["analyst_job"], overall_score=71,
        relevance_score=100, relevance_known=True,
    )

    response = auth(student).get(RECOMMENDATIONS)

    assert response.status_code == 200, response.data
    titles = [row["vacancy_title"] for row in response.data]
    assert "Junior Data Analyst" in titles
    assert "DevOps Engineer" not in titles


def test_relevance_outranks_a_slightly_better_score(auth, student, world):
    """A job in the direction somebody named beats a marginally better-scoring
    one outside it — ordering on score alone ignores the reason the survivors
    survived."""
    from apps.matching.models import MatchResult

    aim(student, world["analyst"])
    MatchResult.objects.create(
        student=student, vacancy=world["devops_job"], overall_score=95,
        relevance_score=40, relevance_known=True,
    )
    MatchResult.objects.create(
        student=student, vacancy=world["analyst_job"], overall_score=70,
        relevance_score=100, relevance_known=True,
    )

    response = auth(student).get(RECOMMENDATIONS)

    assert response.data[0]["vacancy_title"] == "Junior Data Analyst"


def test_a_learner_can_ask_to_see_everything(auth, student, world):
    """Wanting the full list is a reasonable thing to want, and should not
    require guessing at the absence of a query parameter."""
    from apps.matching.models import MatchResult

    aim(student, world["analyst"])
    MatchResult.objects.create(
        student=student, vacancy=world["devops_job"], overall_score=88,
        relevance_score=10, relevance_known=True,
    )

    response = auth(student).get(RECOMMENDATIONS + "?all=1")

    assert [row["vacancy_title"] for row in response.data] == ["DevOps Engineer"]


def test_rows_with_no_declared_interest_stay_in_the_feed(auth, student, world):
    """The flag means the learner told us nothing. Filtering those out would
    empty the page for the people with the least to go on."""
    from apps.matching.models import MatchResult

    aim(student, None, interests=[])
    MatchResult.objects.create(
        student=student, vacancy=world["devops_job"], overall_score=60,
        relevance_score=0, relevance_known=False,
    )

    response = auth(student).get(RECOMMENDATIONS)

    assert [row["vacancy_title"] for row in response.data] == ["DevOps Engineer"]


def test_the_feed_reports_the_reason_it_recommended_something(auth, student, world):
    from apps.matching.models import MatchResult

    aim(student, world["analyst"])
    MatchResult.objects.create(
        student=student, vacancy=world["analyst_job"], overall_score=71,
        relevance_score=100, relevance_known=True,
        relevance_reasons=[{"code": "target_profession", "profession": "Analitik"}],
    )

    row = auth(student).get(RECOMMENDATIONS).data[0]

    assert row["relevance_score"] == 100
    assert row["relevance_reasons"][0]["code"] == "target_profession"
