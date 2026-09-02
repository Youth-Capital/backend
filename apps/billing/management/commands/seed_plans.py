"""Seed the plan catalogue.

Idempotent: re-running updates prices and limits in place rather than
duplicating rows, so this is safe to call from a deploy step.

Prices are in UZS with exponent 0. Soum is not divided in practice, and
pretending it has two decimals would put a permanent ",00" on every price.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.common.enums import Role

from ...enums import BillingInterval, Feature, PlanTier
from ...models import Plan


def _text(uz: str, ru: str, en: str) -> dict:
    return {"uz": uz, "ru": ru, "en": en}


PLANS = [
    # ---------------------------------------------------------------- student
    {
        "code": "student-free",
        "role": Role.STUDENT,
        "tier": PlanTier.FREE,
        "price_minor": 0,
        "interval": BillingInterval.NONE,
        "is_default": True,
        "sort_order": 0,
        "name": _text("Bepul", "Бесплатный", "Free"),
        "description": _text(
            "Profil, kasb tanlash va asosiy tahlil.",
            "Профиль, выбор профессии и базовый анализ.",
            "Profile, career selection and basic analysis.",
        ),
        "limits": {
            Feature.AI_CHAT: 20,
            Feature.COURSE_ENROLLMENT: 3,
            Feature.JOB_APPLICATION: 5,
            Feature.CV_EXPORT: 1,
        },
        "highlights": [
            _text("Profil va kasb tanlash", "Профиль и выбор профессии", "Profile and career selection"),
            _text("3 ta kursga yozilish", "3 курса", "3 course enrolments"),
            _text("Oyiga 5 ta ariza", "5 откликов в месяц", "5 applications per month"),
            _text("Asosiy CV", "Базовое резюме", "Basic CV"),
        ],
    },
    {
        "code": "student-premium",
        "role": Role.STUDENT,
        "tier": PlanTier.PREMIUM,
        "price_minor": 49_000,
        "interval": BillingInterval.MONTH,
        "sort_order": 1,
        "name": _text("Premium", "Премиум", "Premium"),
        "description": _text(
            "AI yordamchi, chuqur tahlil va cheksiz arizalar.",
            "AI-ассистент, глубокий анализ и безлимитные отклики.",
            "AI assistant, deep analysis and unlimited applications.",
        ),
        "limits": {
            Feature.AI_CHAT: None,
            Feature.COURSE_ENROLLMENT: None,
            Feature.JOB_APPLICATION: None,
            Feature.CV_EXPORT: None,
            Feature.AI_ASSISTANT: 200,
            Feature.SKILL_GAP_ADVANCED: None,
            Feature.CAREER_PATH_PERSONAL: None,
            Feature.CV_ANALYSIS: 20,
        },
        "highlights": [
            _text("AI karyera yordamchisi", "AI-ассистент по карьере", "AI career assistant"),
            _text("Chuqur ko'nikma tahlili", "Подробный анализ разрыва навыков", "Advanced skill gap analysis"),
            _text("Shaxsiy karyera yo'li", "Персональный карьерный путь", "Personalised career path"),
            _text("Cheksiz kurs va ariza", "Безлимит курсов и откликов", "Unlimited courses and applications"),
            _text("CV ni AI tahlili", "AI-анализ резюме", "AI CV analysis"),
        ],
    },
    # --------------------------------------------------------------- employer
    {
        "code": "employer-free",
        "role": Role.EMPLOYER,
        "tier": PlanTier.FREE,
        "price_minor": 0,
        "interval": BillingInterval.NONE,
        "is_default": True,
        "sort_order": 0,
        "name": _text("Bepul", "Бесплатный", "Free"),
        "description": _text(
            "Kompaniya profili va bitta ochiq vakansiya.",
            "Профиль компании и одна открытая вакансия.",
            "Company profile and one open vacancy.",
        ),
        "limits": {
            Feature.AI_CHAT: 20,
            Feature.ACTIVE_VACANCY: 1,
            Feature.CANDIDATE_SEARCH: 10,
        },
        "highlights": [
            _text("Kompaniya profili", "Профиль компании", "Company profile"),
            _text("1 ta faol vakansiya", "1 активная вакансия", "1 active vacancy"),
            _text("Oyiga 10 ta qidiruv", "10 поисков в месяц", "10 searches per month"),
        ],
    },
    {
        "code": "employer-pro",
        "role": Role.EMPLOYER,
        "tier": PlanTier.PRO,
        "price_minor": 1_200_000,
        "interval": BillingInterval.MONTH,
        "trial_days": 14,
        "sort_order": 1,
        "name": _text("Pro", "Pro", "Pro"),
        "description": _text(
            "To'liq qidiruv, AI moslashuv va nomzod tahlili.",
            "Полный поиск, AI-подбор и аналитика кандидатов.",
            "Full search, AI matching and candidate analytics.",
        ),
        "limits": {
            Feature.AI_CHAT: 500,
            Feature.ACTIVE_VACANCY: 10,
            Feature.CANDIDATE_SEARCH: 100,
            Feature.CANDIDATE_ANALYTICS: None,
            Feature.EMPLOYER_TEST: 20,
            Feature.EMPLOYER_COURSE: 10,
        },
        "highlights": [
            _text("10 ta faol vakansiya", "10 активных вакансий", "10 active vacancies"),
            _text("Oyiga 100 ta qidiruv", "100 поисков в месяц", "100 searches per month"),
            _text("Nomzod tahlili", "Аналитика кандидатов", "Candidate analytics"),
            _text("Test va kurs yaratish", "Создание тестов и курсов", "Create tests and courses"),
            _text("14 kun bepul sinov", "14 дней бесплатно", "14-day free trial"),
        ],
    },
    {
        "code": "employer-enterprise",
        "role": Role.EMPLOYER,
        "tier": PlanTier.ENTERPRISE,
        "price_minor": 4_500_000,
        "interval": BillingInterval.MONTH,
        "sort_order": 2,
        "name": _text("Enterprise", "Enterprise", "Enterprise"),
        "description": _text(
            "Cheksiz limitlar, talant zaxirasi va maxsus imkoniyatlar.",
            "Безлимитные лимиты, кадровый резерв и особые возможности.",
            "Unlimited quotas, talent pipeline and custom capabilities.",
        ),
        "limits": {
            Feature.AI_CHAT: None,
            Feature.ACTIVE_VACANCY: None,
            Feature.CANDIDATE_SEARCH: None,
            Feature.CANDIDATE_ANALYTICS: None,
            Feature.EMPLOYER_TEST: None,
            Feature.EMPLOYER_COURSE: None,
            Feature.TALENT_PIPELINE: None,
        },
        "highlights": [
            _text("Cheksiz vakansiya va qidiruv", "Безлимит вакансий и поиска", "Unlimited vacancies and search"),
            _text("Talant zaxirasi", "Кадровый резерв", "Talent pipeline"),
            _text("Maxsus kurslar", "Индивидуальные курсы", "Custom courses"),
            _text("Kengaytirilgan tahlil", "Расширенная аналитика", "Advanced analytics"),
        ],
    },
]


class Command(BaseCommand):
    help = "Create or update the subscription plan catalogue."

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = 0

        for spec in PLANS:
            name = spec["name"]
            description = spec["description"]
            defaults = {
                "role": spec["role"],
                "tier": spec["tier"],
                "price_minor": spec["price_minor"],
                "currency": "UZS",
                "currency_exponent": 0,
                "interval": spec["interval"],
                "trial_days": spec.get("trial_days", 0),
                "limits": {str(k): v for k, v in spec["limits"].items()},
                "highlights": spec["highlights"],
                "is_active": True,
                "is_default": spec.get("is_default", False),
                "sort_order": spec["sort_order"],
                "name_uz": name["uz"],
                "name_ru": name["ru"],
                "name_en": name["en"],
                "description_uz": description["uz"],
                "description_ru": description["ru"],
                "description_en": description["en"],
            }

            _plan, was_created = Plan.objects.update_or_create(
                code=spec["code"], defaults=defaults
            )
            created += was_created
            updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(f"Plans: {created} created, {updated} updated.")
        )
