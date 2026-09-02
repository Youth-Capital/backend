"""Shared pytest fixtures."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.common.enums import EvidenceSource, ModerationStatus, RequirementLevel, Role


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def taxonomy(db):
    """Minimal taxonomy: three skills across two categories, one profession."""
    from apps.taxonomy.models import (
        CapitalDimension,
        CapitalDimensionSlug,
        Profession,
        ProfessionSkill,
        Region,
        Skill,
        SkillCategory,
        SkillDimension,
    )

    region = Region.objects.create(code="TAS", name_uz="Toshkent")
    tech = SkillCategory.objects.create(slug="tech", name_uz="Texnologiya")
    data = SkillCategory.objects.create(slug="data", name_uz="Ma'lumotlar", parent=tech)

    python = Skill.objects.create(slug="python", name_uz="Python", category=tech)
    sql = Skill.objects.create(slug="sql", name_uz="SQL", category=data)
    powerbi = Skill.objects.create(slug="power-bi", name_uz="Power BI", category=data)

    digital = CapitalDimension.objects.create(
        slug=CapitalDimensionSlug.DIGITAL_AI, name_uz="Raqamli", order=1
    )
    knowledge = CapitalDimension.objects.create(
        slug=CapitalDimensionSlug.KNOWLEDGE, name_uz="Bilim", order=2
    )
    SkillDimension.objects.create(skill=python, dimension=digital, weight=1)
    SkillDimension.objects.create(skill=sql, dimension=digital, weight=1)
    SkillDimension.objects.create(skill=sql, dimension=knowledge, weight=0.5)

    analyst = Profession.objects.create(slug="data-analyst", name_uz="Analitik")
    ProfessionSkill.objects.create(
        profession=analyst,
        skill=sql,
        requirement=RequirementLevel.REQUIRED,
        min_proficiency=60,
    )
    ProfessionSkill.objects.create(
        profession=analyst,
        skill=powerbi,
        requirement=RequirementLevel.REQUIRED,
        min_proficiency=50,
    )
    ProfessionSkill.objects.create(
        profession=analyst,
        skill=python,
        requirement=RequirementLevel.PREFERRED,
        min_proficiency=40,
    )

    return {
        "region": region,
        "python": python,
        "sql": sql,
        "power_bi": powerbi,
        "profession": analyst,
        "digital": digital,
        "knowledge_dimension": knowledge,
    }


@pytest.fixture
def student(db, taxonomy):
    from apps.profiles.models import StudentProfile
    from apps.profiles.services import generate_youth_id

    user = User.objects.create_user(
        email="student@test.uz", password="TestPass12345", role=Role.STUDENT
    )
    StudentProfile.objects.create(
        user=user,
        youth_id=generate_youth_id(),
        first_name="Test",
        last_name="Student",
        region=taxonomy["region"],
        education_status="UNIVERSITY",
        target_profession=taxonomy["profession"],
    )
    return user


@pytest.fixture
def other_student(db, taxonomy):
    from apps.profiles.models import StudentProfile
    from apps.profiles.services import generate_youth_id

    user = User.objects.create_user(
        email="other@test.uz", password="TestPass12345", role=Role.STUDENT
    )
    StudentProfile.objects.create(
        user=user, youth_id=generate_youth_id(), first_name="Other", last_name="Person"
    )
    return user


@pytest.fixture
def employer(db, taxonomy):
    from apps.profiles.models import EmployerProfile

    user = User.objects.create_user(
        email="employer@test.uz", password="TestPass12345", role=Role.EMPLOYER
    )
    EmployerProfile.objects.create(
        owner=user,
        legal_name="Test LLC",
        brand_name="TestCo",
        slug="testco",
        region=taxonomy["region"],
        verification_status="VERIFIED",
    )
    return user


@pytest.fixture
def other_employer(db):
    from apps.profiles.models import EmployerProfile

    user = User.objects.create_user(
        email="rival@test.uz", password="TestPass12345", role=Role.EMPLOYER
    )
    EmployerProfile.objects.create(
        owner=user, legal_name="Rival LLC", brand_name="RivalCo", slug="rivalco"
    )
    return user


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        email="admin@test.uz", password="TestPass12345"
    )


@pytest.fixture
def vacancy(db, employer, taxonomy):
    from apps.jobs.models import Vacancy, VacancySkill

    vacancy = Vacancy.objects.create(
        employer=employer.employer_profile,
        title="Junior Data Analyst",
        description="Test vacancy",
        region=taxonomy["region"],
        min_experience_months=0,
        status=ModerationStatus.PUBLISHED,
    )
    VacancySkill.objects.create(
        vacancy=vacancy,
        skill=taxonomy["sql"],
        requirement=RequirementLevel.REQUIRED,
        min_knowledge_score=60,
    )
    VacancySkill.objects.create(
        vacancy=vacancy,
        skill=taxonomy["power_bi"],
        requirement=RequirementLevel.REQUIRED,
        min_knowledge_score=50,
    )
    return vacancy


@pytest.fixture
def auth(api):
    """Authenticate the client as a given user."""

    def _auth(user):
        from apps.accounts.authentication import issue_tokens

        access, _refresh = issue_tokens(user)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return api

    return _auth


@pytest.fixture
def give_skill(db):
    """Attach a skill with a chosen evidence source."""

    def _give(user, skill, score, source=EvidenceSource.TEST):
        from apps.profiles.services import record_skill_evidence

        return record_skill_evidence(
            user=user, skill=skill, source=source, score=score, note="test fixture"
        )

    return _give
