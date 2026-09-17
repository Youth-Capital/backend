"""Demo data (prompt §30).

Deliberately *uneven*: students are given different skills, different levels
and different evidence quality, so the matching engine produces a real spread
instead of everyone scoring the same. A seed where every candidate matches 85%
proves nothing.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import ConsentType, User
from apps.assessment.models import (
    AnswerOption,
    Question,
    QuestionType,
    Test,
    TestSkill,
    TestType,
)
from apps.common.enums import EvidenceSource, ModerationStatus, Role
from apps.common.recompute import recompute_for_user
from apps.cv.models import CVDocument, PortfolioItem
from apps.experience.models import Experience, ExperienceSkill, ExperienceType
from apps.jobs.models import (
    EducationRequirement,
    EmploymentType,
    Vacancy,
    VacancySkill,
    WorkMode,
)
from apps.learning.models import (
    Course,
    CourseLevel,
    CourseModule,
    CourseSkill,
    Lesson,
    ProviderType,
)
from apps.profiles.models import (
    EducationStatus,
    EmployerProfile,
    EmploymentStatus,
    StudentProfile,
)
from apps.taxonomy.models import Profession, Region, Skill

DEMO_PASSWORD = "YoshlarDemo2026!"

EMPLOYERS = [
    ("it@demo.uz", "Nexora Digital", "IT services", "TAS",
     "Full-cycle software house building products for local and export markets."),
    ("fintech@demo.uz", "PayNur Fintech", "Fintech", "TAS",
     "Digital payments and lending platform serving 400k customers."),
    ("cyber@demo.uz", "SafeNet Security", "Cybersecurity", "SAM",
     "Security operations centre and audit services for enterprise clients."),
]

# title, provider index (None = platform), level, minutes, [(skill, target)], modules
COURSES = [
    ("Python Fundamentals", None, CourseLevel.BEGINNER, 720,
     [("python", 60), ("problem-solving", 45)],
     ["Syntax and types", "Control flow", "Functions and modules", "Working with files"]),
    ("SQL for Data Analysis", None, CourseLevel.BEGINNER, 600,
     [("sql", 70), ("data-analysis", 50)],
     ["Relational basics", "SELECT and filtering", "Joins", "Aggregation and windows"]),
    ("JavaScript Fundamentals", None, CourseLevel.BEGINNER, 660,
     [("javascript", 65), ("html-css", 55)],
     ["Language basics", "The DOM", "Async JavaScript", "Tooling"]),
    ("React in Practice", 0, CourseLevel.INTERMEDIATE, 840,
     [("react", 65), ("typescript", 50), ("javascript", 70)],
     ["Components and props", "State and hooks", "Routing and data", "Testing"]),
    ("Web Security Basics", 2, CourseLevel.BEGINNER, 540,
     [("web-security", 60), ("networking", 50), ("linux", 45)],
     ["Threat landscape", "OWASP Top 10", "Secure configuration", "Incident basics"]),
    ("Financial Literacy for Youth", None, CourseLevel.BEGINNER, 420,
     [("financial-literacy", 70), ("budgeting", 60)],
     ["Money and budgeting", "Saving", "Credit and risk", "First investments"]),
    ("Power BI Essentials", 1, CourseLevel.INTERMEDIATE, 480,
     [("power-bi", 65), ("data-analysis", 55), ("excel", 60)],
     ["Data modelling", "DAX basics", "Visual design", "Publishing reports"]),
]

# title, employer index, [(skill, requirement, min)], test?, type, months, education
VACANCIES = [
    ("Junior Backend Developer", 0, [
        ("python", "REQUIRED", 60), ("sql", "REQUIRED", 55), ("rest-api", "REQUIRED", 55),
        ("git", "REQUIRED", 50), ("django", "PREFERRED", 50), ("english", "PREFERRED", 45),
    ], EmploymentType.FULL_TIME, 6, EducationRequirement.NONE, WorkMode.HYBRID, "TAS"),
    ("Junior Frontend Developer", 0, [
        ("javascript", "REQUIRED", 60), ("react", "REQUIRED", 55), ("html-css", "REQUIRED", 60),
        ("git", "REQUIRED", 45), ("typescript", "PREFERRED", 45),
    ], EmploymentType.FULL_TIME, 3, EducationRequirement.NONE, WorkMode.ONSITE, "TAS"),
    ("Frontend Intern", 0, [
        ("html-css", "REQUIRED", 45), ("javascript", "REQUIRED", 40),
        ("git", "PREFERRED", 30),
    ], EmploymentType.INTERNSHIP, 0, EducationRequirement.NONE, WorkMode.ONSITE, "TAS"),
    ("Junior Data Analyst", 1, [
        ("sql", "REQUIRED", 65), ("excel", "REQUIRED", 55), ("power-bi", "REQUIRED", 50),
        ("statistics", "REQUIRED", 50), ("python", "PREFERRED", 45),
        ("english", "PREFERRED", 50),
    ], EmploymentType.FULL_TIME, 6, EducationRequirement.UNIVERSITY, WorkMode.HYBRID, "TAS"),
    ("Data Analyst Intern", 1, [
        ("sql", "REQUIRED", 40), ("excel", "REQUIRED", 45),
        ("statistics", "PREFERRED", 35),
    ], EmploymentType.INTERNSHIP, 0, EducationRequirement.NONE, WorkMode.REMOTE, "TAS"),
    ("Junior Cybersecurity Analyst", 2, [
        ("linux", "REQUIRED", 60), ("networking", "REQUIRED", 60), ("siem", "REQUIRED", 50),
        ("web-security", "PREFERRED", 55), ("python", "PREFERRED", 40),
    ], EmploymentType.FULL_TIME, 12, EducationRequirement.COLLEGE, WorkMode.ONSITE, "SAM"),
    ("Cybersecurity Intern", 2, [
        ("linux", "REQUIRED", 40), ("networking", "REQUIRED", 40),
        ("web-security", "PREFERRED", 35),
    ], EmploymentType.INTERNSHIP, 0, EducationRequirement.NONE, WorkMode.ONSITE, "SAM"),
    ("Accountant (Junior)", 1, [
        ("accounting", "REQUIRED", 60), ("excel", "REQUIRED", 55),
        ("financial-literacy", "REQUIRED", 55),
    ], EmploymentType.FULL_TIME, 6, EducationRequirement.COLLEGE, WorkMode.ONSITE, "TAS"),
]

# email, first, last, region, education, profession slug,
# [(skill, level, source)], experience entries
STUDENTS = [
    ("aziza@demo.uz", "Aziza", "Yusupova", "TAS", EducationStatus.UNIVERSITY, "data-analyst", [
        ("sql", 78, EvidenceSource.TEST), ("excel", 82, EvidenceSource.TEST),
        ("python", 55, EvidenceSource.COURSE), ("statistics", 60, EvidenceSource.TEST),
        ("english", 74, EvidenceSource.SELF), ("power-bi", 35, EvidenceSource.SELF),
    ], [("INTERNSHIP", "Data Intern", "PayNur Fintech", 8, ["sql", "excel"])]),
    ("bekzod@demo.uz", "Bekzod", "Ergashev", "TAS", EducationStatus.UNIVERSITY, "software-developer", [
        ("python", 84, EvidenceSource.TEST), ("sql", 68, EvidenceSource.TEST),
        ("git", 72, EvidenceSource.COURSE), ("rest-api", 66, EvidenceSource.COURSE),
        ("django", 61, EvidenceSource.COURSE), ("english", 58, EvidenceSource.SELF),
    ], [("WORK", "Junior Developer", "Freelance", 14, ["python", "sql", "rest-api"])]),
    ("dilnoza@demo.uz", "Dilnoza", "Sobirova", "SAM", EducationStatus.COLLEGE, "cybersecurity-specialist", [
        ("linux", 71, EvidenceSource.TEST), ("networking", 68, EvidenceSource.TEST),
        ("web-security", 62, EvidenceSource.COURSE), ("siem", 44, EvidenceSource.SELF),
        ("python", 38, EvidenceSource.SELF),
    ], [("PROJECT", "Home SOC lab", "Personal", 6, ["linux", "networking"])]),
    ("elyor@demo.uz", "Elyor", "Nazarov", "TAS", EducationStatus.UNIVERSITY, "frontend-developer", [
        ("javascript", 76, EvidenceSource.TEST), ("html-css", 81, EvidenceSource.TEST),
        ("react", 64, EvidenceSource.COURSE), ("git", 58, EvidenceSource.COURSE),
        ("typescript", 41, EvidenceSource.SELF),
    ], [("FREELANCE", "Landing pages", "Self-employed", 10, ["javascript", "html-css"])]),
    ("feruza@demo.uz", "Feruza", "Qodirova", "FAR", EducationStatus.SCHOOL, "ux-ui-designer", [
        ("figma", 66, EvidenceSource.COURSE), ("ui-design", 58, EvidenceSource.SELF),
        ("communication", 70, EvidenceSource.COURSE),
    ], [("COMPETITION", "Regional design contest", "Yoshlar Ittifoqi", 1, ["ui-design"])]),
    ("gulnora@demo.uz", "Gulnora", "Tashkentova", "AND", EducationStatus.GRADUATE, "accountant", [
        ("accounting", 74, EvidenceSource.TEST), ("excel", 69, EvidenceSource.TEST),
        ("financial-literacy", 71, EvidenceSource.COURSE),
    ], [("WORK", "Assistant accountant", "Local retail", 18, ["accounting", "excel"])]),
    ("hasan@demo.uz", "Hasan", "Umarov", "TAS", EducationStatus.UNIVERSITY, "data-analyst", [
        ("sql", 44, EvidenceSource.SELF), ("excel", 52, EvidenceSource.SELF),
    ], []),
    ("iroda@demo.uz", "Iroda", "Bekmurodova", "BUX", EducationStatus.COLLEGE, "digital-marketer", [
        ("smm", 68, EvidenceSource.COURSE), ("content-marketing", 61, EvidenceSource.COURSE),
        ("communication", 75, EvidenceSource.COURSE), ("seo", 40, EvidenceSource.SELF),
    ], [("VOLUNTEER", "Social media for NGO", "Ekoloji", 12, ["smm"])]),
    ("jahongir@demo.uz", "Jahongir", "Alimov", "NAM", EducationStatus.SCHOOL, "software-developer", [
        ("python", 47, EvidenceSource.COURSE), ("problem-solving", 55, EvidenceSource.SELF),
    ], [("HACKATHON", "IT Park hackathon", "IT Park", 1, ["python"])]),
    ("kamola@demo.uz", "Kamola", "Ismoilova", "TAS", EducationStatus.UNIVERSITY, "frontend-developer", [
        ("javascript", 58, EvidenceSource.COURSE), ("html-css", 63, EvidenceSource.COURSE),
        ("react", 35, EvidenceSource.SELF), ("english", 66, EvidenceSource.TEST),
    ], []),
    ("laziz@demo.uz", "Laziz", "Xolmatov", "SAM", EducationStatus.GRADUATE, "cybersecurity-specialist", [
        ("linux", 82, EvidenceSource.TEST), ("networking", 79, EvidenceSource.TEST),
        ("siem", 68, EvidenceSource.EMPLOYER), ("web-security", 74, EvidenceSource.TEST),
        ("incident-response", 60, EvidenceSource.COURSE), ("english", 62, EvidenceSource.SELF),
    ], [("WORK", "SOC Analyst L1", "SafeNet Security", 22, ["linux", "siem", "networking"])]),
    ("madina@demo.uz", "Madina", "Rustamova", "FAR", EducationStatus.UNIVERSITY, "entrepreneur", [
        ("entrepreneurship", 64, EvidenceSource.COURSE), ("sales", 58, EvidenceSource.SELF),
        ("financial-literacy", 66, EvidenceSource.COURSE), ("business-model", 55, EvidenceSource.COURSE),
    ], [("PROJECT", "Handmade marketplace", "Self-employed", 9, ["sales", "entrepreneurship"])]),
    ("nodir@demo.uz", "Nodir", "Sultonov", "TAS", EducationStatus.NONE, "software-developer", [
        ("python", 62, EvidenceSource.TEST), ("git", 49, EvidenceSource.SELF),
        ("sql", 51, EvidenceSource.COURSE),
    ], [("FREELANCE", "Automation scripts", "Self-employed", 7, ["python"])]),
    ("oybek@demo.uz", "Oybek", "Yo'ldoshev", "AND", EducationStatus.COLLEGE, "data-analyst", [
        ("excel", 71, EvidenceSource.TEST), ("sql", 58, EvidenceSource.COURSE),
        ("power-bi", 62, EvidenceSource.COURSE), ("statistics", 45, EvidenceSource.SELF),
    ], [("INTERNSHIP", "Reporting intern", "Regional bank", 5, ["excel", "power-bi"])]),
    ("shahnoza@demo.uz", "Shahnoza", "Mirzayeva", "TAS", EducationStatus.SCHOOL, None, [
        ("english", 71, EvidenceSource.TEST), ("communication", 64, EvidenceSource.SELF),
    ], []),
]


class Command(BaseCommand):
    help = "Create demo employers, students, courses, tests and vacancies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wipe", action="store_true", help="Delete existing demo accounts first."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20261)  # reproducible demo data

        if not Skill.objects.exists():
            self.stderr.write(
                self.style.ERROR("Run `manage.py seed_taxonomy` first — no skills found.")
            )
            return

        if options["wipe"]:
            deleted, _ = User.objects.filter(email__endswith="@demo.uz").delete()
            self.stdout.write(f"Removed {deleted} demo objects.")

        self.skills = {s.slug: s for s in Skill.objects.all()}
        self.professions = {p.slug: p for p in Profession.objects.all()}
        self.regions = {r.code: r for r in Region.objects.all()}

        employers = self._create_employers()
        courses = self._create_courses(employers)
        self._create_tests(courses, employers)
        self._create_vacancies(employers)
        self._create_students()
        self._compute_matches()

        self.stdout.write(
            self.style.SUCCESS(
                "\nDemo data ready.\n"
                f"  Employers: {len(employers)}   "
                f"Students: {len(STUDENTS)}\n"
                f"  Courses: {len(courses)}   Vacancies: {Vacancy.objects.count()}\n"
                f"  Password for every demo account: {DEMO_PASSWORD}\n"
                f"  Try:  aziza@demo.uz (data analyst track), "
                f"laziz@demo.uz (strong cyber candidate), "
                f"hasan@demo.uz (early beginner)\n"
            )
        )

    # -- helpers ---------------------------------------------------------
    def _user(self, email: str, role: str) -> User:
        user = User.objects.filter(email=email).first()
        if user is not None:
            return user
        user = User.objects.create_user(
            email=email, password=DEMO_PASSWORD, role=role, email_verified=True
        )
        from apps.accounts.services import grant_consent

        for consent in (
            ConsentType.TERMS,
            ConsentType.PRIVACY,
            ConsentType.DATA_PROCESSING,
            ConsentType.AI_PROCESSING,
            ConsentType.TALENT_SEARCH,
        ):
            grant_consent(user, consent)
        return user

    def _create_employers(self) -> list[EmployerProfile]:
        profiles = []
        for email, name, industry, region_code, description in EMPLOYERS:
            user = self._user(email, Role.EMPLOYER)
            profile, _ = EmployerProfile.objects.update_or_create(
                owner=user,
                defaults={
                    "legal_name": f'"{name}" MChJ',
                    "brand_name": name,
                    "slug": name.lower().replace(" ", "-"),
                    "industry": industry,
                    "description": description,
                    "region": self.regions.get(region_code),
                    "contact_email": email,
                    "verification_status": "VERIFIED",
                    "verified_at": timezone.now(),
                },
            )
            profiles.append(profile)
        return profiles

    def _create_courses(self, employers) -> list[Course]:
        created = []
        for title, employer_index, level, minutes, skill_links, module_titles in COURSES:
            employer = employers[employer_index] if employer_index is not None else None
            author = employer.owner if employer else self._admin()
            category = self.skills[skill_links[0][0]].category

            course, _ = Course.objects.update_or_create(
                slug=title.lower().replace(" ", "-"),
                defaults={
                    "title": title,
                    "summary": f"{title} — practical course with assessments.",
                    "description": (
                        f"{title}. Hands-on lessons, a graded test and skills that "
                        f"land on your profile when you finish."
                    ),
                    "provider_type": ProviderType.EMPLOYER if employer else ProviderType.PLATFORM,
                    "employer": employer,
                    "author": author,
                    "category": category,
                    "level": level,
                    "duration_minutes": minutes,
                    "status": ModerationStatus.PUBLISHED,
                    "published_at": timezone.now(),
                    "is_certified": True,
                    "rating_avg": round(random.uniform(4.0, 4.9), 2),
                    "rating_count": random.randint(12, 140),
                },
            )
            for slug, target in skill_links:
                CourseSkill.objects.update_or_create(
                    course=course,
                    skill=self.skills[slug],
                    defaults={"target_proficiency": target},
                )
            for order, module_title in enumerate(module_titles):
                module, _ = CourseModule.objects.update_or_create(
                    course=course, title=module_title, defaults={"order": order}
                )
                for lesson_index in range(3):
                    Lesson.objects.update_or_create(
                        module=module,
                        title=f"{module_title} — part {lesson_index + 1}",
                        defaults={
                            "content": (
                                f"Lesson material for {module_title}, part "
                                f"{lesson_index + 1}."
                            ),
                            "duration_minutes": minutes // (len(module_titles) * 3),
                            "order": lesson_index,
                            "is_free_preview": order == 0 and lesson_index == 0,
                        },
                    )
            created.append(course)
        return created

    def _admin(self) -> User:
        admin = User.objects.filter(role=Role.ADMIN).first()
        if admin is None:
            admin = User.objects.create_superuser(
                email="admin@demo.uz", password=DEMO_PASSWORD
            )
        return admin

    def _create_tests(self, courses, employers):
        for course in courses:
            test, created = Test.objects.get_or_create(
                title=f"{course.title} — assessment",
                defaults={
                    "description": f"Checks what you learned in {course.title}.",
                    "type": TestType.COURSE_TEST,
                    "course": course,
                    "employer": course.employer,
                    "author": course.author,
                    "passing_score": 60,
                    "time_limit_minutes": 20,
                    "max_attempts": 3,
                    "status": ModerationStatus.PUBLISHED,
                    "published_at": timezone.now(),
                },
            )
            if not created:
                continue

            for link in course.skill_links.select_related("skill"):
                TestSkill.objects.get_or_create(test=test, skill=link.skill)
                for index in range(3):
                    question = Question.objects.create(
                        test=test,
                        text=f"[{link.skill.name}] Practice question {index + 1}",
                        type=QuestionType.SINGLE,
                        points=1,
                        order=index,
                        skill=link.skill,
                        explanation="See the course module for the reasoning.",
                    )
                    for option_index in range(4):
                        AnswerOption.objects.create(
                            question=question,
                            text=f"Option {option_index + 1}",
                            is_correct=option_index == 0,
                            order=option_index,
                        )

    def _create_vacancies(self, employers):
        today = timezone.localdate()
        for (
            title,
            employer_index,
            skill_links,
            employment_type,
            months,
            education,
            work_mode,
            region_code,
        ) in VACANCIES:
            employer = employers[employer_index]
            profession = self._guess_profession(skill_links)

            vacancy, created = Vacancy.objects.get_or_create(
                employer=employer,
                title=title,
                defaults={
                    "description": (
                        f"{employer.display_name} is hiring a {title}. You will work "
                        f"on real tasks, from week one."
                    ),
                    "responsibilities": "Deliver assigned tasks, review, learn, ship.",
                    "conditions": "Official employment, learning budget.",
                    "employment_type": employment_type,
                    "work_mode": work_mode,
                    "region": self.regions.get(region_code),
                    "city": "Toshkent" if region_code == "TAS" else "Samarqand",
                    "profession": profession,
                    "min_experience_months": months,
                    "education_required": education,
                    "salary_min": None if employment_type == EmploymentType.INTERNSHIP else 6_000_000,
                    "salary_max": None if employment_type == EmploymentType.INTERNSHIP else 14_000_000,
                    "is_salary_public": True,
                    "deadline": today + timedelta(days=45),
                    "status": ModerationStatus.PUBLISHED,
                    "published_at": timezone.now(),
                },
            )
            if not created:
                continue
            for order, (slug, requirement, min_score) in enumerate(skill_links):
                VacancySkill.objects.update_or_create(
                    vacancy=vacancy,
                    skill=self.skills[slug],
                    defaults={
                        "requirement": requirement,
                        "min_knowledge_score": min_score,
                        "order": order,
                    },
                )

    def _guess_profession(self, skill_links):
        """Attach the profession whose required skills overlap most."""
        wanted = {slug for slug, _r, _m in skill_links}
        best, best_overlap = None, 0
        for profession in self.professions.values():
            slugs = {link.skill.slug for link in profession.skill_links.all()}
            overlap = len(slugs & wanted)
            if overlap > best_overlap:
                best, best_overlap = profession, overlap
        return best

    def _create_students(self):
        from apps.profiles.services import generate_youth_id, record_skill_evidence

        for (
            email,
            first,
            last,
            region_code,
            education,
            profession_slug,
            skill_entries,
            experiences,
        ) in STUDENTS:
            user = self._user(email, Role.STUDENT)
            profile, _ = StudentProfile.objects.update_or_create(
                user=user,
                defaults={
                    "youth_id": getattr(
                        StudentProfile.objects.filter(user=user).first(),
                        "youth_id",
                        None,
                    )
                    or generate_youth_id(),
                    "first_name": first,
                    "last_name": last,
                    # Anchored to today, so demo learners stay 18-26 as the calendar moves.
                    "birth_date": date(
                        date.today().year - random.randint(18, 26),
                        random.randint(1, 12),
                        15,
                    ),
                    "region": self.regions.get(region_code),
                    "city": "Toshkent" if region_code == "TAS" else "—",
                    "education_status": education,
                    "institution": "Toshkent axborot texnologiyalari universiteti"
                    if education == EducationStatus.UNIVERSITY
                    else "",
                    "target_profession": self.professions.get(profession_slug),
                    "employment_status": EmploymentStatus.LOOKING,
                    "open_to_work": True,
                    "languages": [{"code": "uz", "level": "native"}, {"code": "ru", "level": "B2"}],
                    "onboarding_completed_at": timezone.now(),
                    "diagnostics_completed_at": timezone.now(),
                },
            )

            for slug, level, source in skill_entries:
                skill = self.skills.get(slug)
                if skill is None:
                    continue
                record_skill_evidence(
                    user=user,
                    skill=skill,
                    source=source,
                    score=level,
                    ref_type="Seed",
                    note=f"Demo evidence ({source})",
                )

            for exp_type, title, organization, months, exp_skills in experiences:
                end = timezone.localdate() - timedelta(days=30)
                experience, _ = Experience.objects.update_or_create(
                    user=user,
                    title=title,
                    organization=organization,
                    defaults={
                        "type": getattr(ExperienceType, exp_type),
                        "description": f"{title} at {organization}.",
                        "start_date": end - timedelta(days=30 * months),
                        "end_date": end,
                        "is_current": False,
                    },
                )
                for slug in exp_skills:
                    if slug in self.skills:
                        ExperienceSkill.objects.get_or_create(
                            experience=experience, skill=self.skills[slug]
                        )

            recompute_for_user(user, reason="seed")
            self._create_cv(user, profile, first)
            self.stdout.write(f"  student {email} ready")

    def _create_cv(self, user, profile, first_name: str) -> None:
        """A primary CV per learner, rated.

        Without one the CV rating has nothing to show, and the employer's
        candidate card reads as if every demo learner never wrote a résumé.
        The score is deliberately *not* padded: it is computed from the same
        skills, evidence and experience the seeder just created, so aziza
        (tested skills, an internship) and hasan (self-declared only) land far
        apart — which is the contrast the demo exists to make.
        """
        from apps.cv.rating import refresh_cv_rating

        profession = getattr(profile, "target_profession", None)
        headline = profession.name if profession else "Yoshlar Kapitali"

        cv, _created = CVDocument.objects.update_or_create(
            user=user,
            title="Mening rezyumem",
            defaults={
                "headline": headline,
                "summary": (
                    f"{first_name}. {headline} yo'nalishida rivojlanyapman: "
                    "kurslarni tugatdim, ko'nikmalarni testlar bilan "
                    "tasdiqladim va amaliy tajriba to'pladim. Jamoada ishlash "
                    "va yangi vositalarni tez o'rganish men uchun oson."
                ),
                "target_profession": profession,
                "is_primary": True,
            },
        )

        # One portfolio item for the learners who have a project on record, so
        # the portfolio component is not uniformly zero across the demo.
        experience = user.experiences.filter(type=ExperienceType.PROJECT).first()
        if experience is not None:
            PortfolioItem.objects.update_or_create(
                user=user,
                title=experience.title,
                defaults={
                    "description": experience.description,
                    "url": "https://github.com/yoshlar-kapitali",
                    "is_public": True,
                },
            )

        refresh_cv_rating(cv)

    def _compute_matches(self):
        """Score every published vacancy against the candidate pool.

        Matching is normally lazy — computed when a vacancy is published
        through moderation, or on first read. The seeder inserts vacancies
        already published, so without this the demo opens with "0 matching
        vacancies" and the product looks broken on first login.
        """
        from apps.matching.services import recompute_matches_for_vacancy

        total = 0
        for vacancy in Vacancy.objects.filter(status=ModerationStatus.PUBLISHED):
            total += recompute_matches_for_vacancy(vacancy)
        self.stdout.write(f"  computed {total} match results")
