"""Seed the reference data the platform cannot function without.

Separate from `seed_demo` on purpose: taxonomy is real production data
(skills, professions, the nine capital axes), while demo users and vacancies
are throwaway. A production deploy runs this one and not the other.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.capital.models import CapitalWeightConfig
from apps.knowledge.models import KnowledgeConfig
from apps.matching.models import DEFAULT_WEIGHTS, LEGACY_PROMPT_WEIGHTS, MatchWeightProfile
from apps.taxonomy.models import (
    CapitalDimension,
    CapitalDimensionSlug,
    DemandLevel,
    Profession,
    ProfessionSkill,
    Region,
    Skill,
    SkillCategory,
    SkillDimension,
)

REGIONS = [
    ("TAS", "Toshkent shahri", "город Ташкент", "Tashkent city"),
    ("SAM", "Samarqand viloyati", "Самаркандская область", "Samarkand region"),
    ("FAR", "Farg'ona viloyati", "Ферганская область", "Fergana region"),
    ("AND", "Andijon viloyati", "Андижанская область", "Andijan region"),
    ("BUX", "Buxoro viloyati", "Бухарская область", "Bukhara region"),
    ("NAM", "Namangan viloyati", "Наманганская область", "Namangan region"),
]

#: Categories whose whole branch holds behavioural competencies rather than
#: technical ability. Read by apps/taxonomy/services.soft_skill_ids(), which is
#: what separates the two halves of the capability report.
SOFT_SKILL_CATEGORIES = {"soft-skills"}

# slug, (uz, ru, en), parent slug
CATEGORIES = [
    ("technology", ("Texnologiya", "Технологии", "Technology"), None),
    ("programming", ("Dasturlash", "Программирование", "Programming"), "technology"),
    ("data", ("Ma'lumotlar", "Данные", "Data"), "technology"),
    ("cybersecurity", ("Kiberxavfsizlik", "Кибербезопасность", "Cybersecurity"), "technology"),
    ("design", ("Dizayn", "Дизайн", "Design"), None),
    ("business", ("Biznes", "Бизнес", "Business"), None),
    ("marketing", ("Marketing", "Маркетинг", "Marketing"), "business"),
    ("finance", ("Moliya", "Финансы", "Finance"), "business"),
    ("soft-skills", ("Yumshoq ko'nikmalar", "Гибкие навыки", "Soft skills"), None),
    ("languages", ("Tillar", "Языки", "Languages"), None),
    ("civic", ("Fuqarolik", "Гражданские", "Civic"), None),
    ("wellbeing", ("Sog'lom hayot", "Здоровый образ жизни", "Wellbeing"), None),
]

# slug, (uz, ru, en), category slug, aliases, [(dimension, weight)]
SKILLS = [
    ("python", ("Python", "Python", "Python"), "programming", ["py", "python3"],
     [("DIGITAL_AI", 1.0), ("PROFESSIONAL", 0.8)]),
    ("javascript", ("JavaScript", "JavaScript", "JavaScript"), "programming", ["js", "ecmascript"],
     [("DIGITAL_AI", 1.0), ("PROFESSIONAL", 0.8)]),
    ("typescript", ("TypeScript", "TypeScript", "TypeScript"), "programming", ["ts"],
     [("DIGITAL_AI", 0.9), ("PROFESSIONAL", 0.8)]),
    ("react", ("React", "React", "React"), "programming", ["reactjs"],
     [("DIGITAL_AI", 0.9), ("PROFESSIONAL", 0.9)]),
    ("django", ("Django", "Django", "Django"), "programming", [],
     [("DIGITAL_AI", 0.9), ("PROFESSIONAL", 0.9)]),
    ("html-css", ("HTML va CSS", "HTML и CSS", "HTML & CSS"), "programming", ["html", "css"],
     [("DIGITAL_AI", 0.7)]),
    ("git", ("Git", "Git", "Git"), "programming", ["version control"],
     [("DIGITAL_AI", 0.6), ("PROFESSIONAL", 0.6)]),
    ("rest-api", ("REST API", "REST API", "REST API"), "programming", ["api"],
     [("DIGITAL_AI", 0.8), ("PROFESSIONAL", 0.8)]),
    ("sql", ("SQL", "SQL", "SQL"), "data", ["postgres", "mysql"],
     [("DIGITAL_AI", 1.0), ("KNOWLEDGE", 0.7)]),
    ("excel", ("Excel", "Excel", "Excel"), "data", ["spreadsheets"],
     [("DIGITAL_AI", 0.6), ("PROFESSIONAL", 0.5)]),
    ("power-bi", ("Power BI", "Power BI", "Power BI"), "data", ["powerbi", "bi"],
     [("DIGITAL_AI", 0.8)]),
    ("statistics", ("Statistika", "Статистика", "Statistics"), "data", ["stats"],
     [("KNOWLEDGE", 1.0)]),
    ("data-analysis", ("Ma'lumotlar tahlili", "Анализ данных", "Data analysis"), "data", [],
     [("DIGITAL_AI", 0.9), ("KNOWLEDGE", 0.8)]),
    ("machine-learning", ("Mashinali o'qitish", "Машинное обучение", "Machine learning"), "data", ["ml", "ai"],
     [("DIGITAL_AI", 1.0), ("KNOWLEDGE", 0.9)]),
    ("linux", ("Linux", "Linux", "Linux"), "cybersecurity", ["unix"],
     [("DIGITAL_AI", 0.8)]),
    ("networking", ("Tarmoqlar", "Сети", "Networking"), "cybersecurity", ["tcp/ip"],
     [("DIGITAL_AI", 0.8), ("KNOWLEDGE", 0.6)]),
    ("web-security", ("Veb xavfsizlik", "Веб-безопасность", "Web security"), "cybersecurity", ["owasp"],
     [("DIGITAL_AI", 0.9)]),
    ("siem", ("SIEM", "SIEM", "SIEM"), "cybersecurity", ["splunk"],
     [("DIGITAL_AI", 0.8), ("PROFESSIONAL", 0.7)]),
    ("incident-response", ("Insidentlarga javob", "Реагирование на инциденты", "Incident response"),
     "cybersecurity", [], [("PROFESSIONAL", 0.9)]),
    ("ui-design", ("UI dizayn", "UI дизайн", "UI design"), "design", ["interface design"],
     [("PROFESSIONAL", 0.9)]),
    ("ux-research", ("UX tadqiqot", "UX исследования", "UX research"), "design", ["user research"],
     [("PROFESSIONAL", 0.8), ("KNOWLEDGE", 0.6)]),
    ("figma", ("Figma", "Figma", "Figma"), "design", [],
     [("DIGITAL_AI", 0.7), ("PROFESSIONAL", 0.7)]),
    ("prototyping", ("Prototiplash", "Прототипирование", "Prototyping"), "design", [],
     [("PROFESSIONAL", 0.7)]),
    ("seo", ("SEO", "SEO", "SEO"), "marketing", [],
     [("PROFESSIONAL", 0.7), ("ENTREPRENEURIAL", 0.5)]),
    ("smm", ("SMM", "SMM", "Social media marketing"), "marketing", ["social media"],
     [("PROFESSIONAL", 0.7), ("SOCIAL", 0.5)]),
    ("content-marketing", ("Kontent marketing", "Контент-маркетинг", "Content marketing"),
     "marketing", [], [("PROFESSIONAL", 0.7), ("ENTREPRENEURIAL", 0.5)]),
    ("analytics-marketing", ("Marketing tahlili", "Маркетинговая аналитика", "Marketing analytics"),
     "marketing", ["ga4"], [("DIGITAL_AI", 0.6), ("PROFESSIONAL", 0.7)]),
    ("accounting", ("Buxgalteriya", "Бухгалтерия", "Accounting"), "finance", [],
     [("FINANCIAL", 1.0), ("PROFESSIONAL", 0.8)]),
    ("financial-literacy", ("Moliyaviy savodxonlik", "Финансовая грамотность", "Financial literacy"),
     "finance", [], [("FINANCIAL", 1.0)]),
    ("budgeting", ("Byudjetlashtirish", "Бюджетирование", "Budgeting"), "finance", [],
     [("FINANCIAL", 0.9)]),
    ("investment-basics", ("Investitsiya asoslari", "Основы инвестиций", "Investment basics"),
     "finance", [], [("FINANCIAL", 0.9), ("ENTREPRENEURIAL", 0.5)]),
    ("business-model", ("Biznes model", "Бизнес-модель", "Business model"), "business", [],
     [("ENTREPRENEURIAL", 1.0)]),
    ("sales", ("Savdo", "Продажи", "Sales"), "business", [],
     [("ENTREPRENEURIAL", 0.9), ("SOCIAL", 0.6)]),
    ("project-management", ("Loyiha boshqaruvi", "Управление проектами", "Project management"),
     "business", ["pm", "scrum"], [("PROFESSIONAL", 0.9), ("PERSONAL_ETHICAL", 0.5)]),
    ("entrepreneurship", ("Tadbirkorlik", "Предпринимательство", "Entrepreneurship"),
     "business", [], [("ENTREPRENEURIAL", 1.0)]),
    ("communication", ("Kommunikatsiya", "Коммуникация", "Communication"), "soft-skills", [],
     [("SOCIAL", 1.0), ("PERSONAL_ETHICAL", 0.6)]),
    ("teamwork", ("Jamoada ishlash", "Работа в команде", "Teamwork"), "soft-skills", [],
     [("SOCIAL", 1.0)]),
    ("leadership", ("Liderlik", "Лидерство", "Leadership"), "soft-skills", [],
     [("PERSONAL_ETHICAL", 1.0), ("SOCIAL", 0.8)]),
    ("time-management", ("Vaqtni boshqarish", "Тайм-менеджмент", "Time management"),
     "soft-skills", [], [("PERSONAL_ETHICAL", 1.0), ("HEALTH", 0.4)]),
    ("critical-thinking", ("Tanqidiy fikrlash", "Критическое мышление", "Critical thinking"),
     "soft-skills", [], [("KNOWLEDGE", 0.8), ("PERSONAL_ETHICAL", 0.6)]),
    ("problem-solving", ("Muammo yechish", "Решение проблем", "Problem solving"),
     "soft-skills", [], [("KNOWLEDGE", 0.8), ("ENTREPRENEURIAL", 0.6)]),
    ("presentation", ("Prezentatsiya", "Презентация", "Presentation"), "soft-skills", [],
     [("SOCIAL", 0.8)]),
    ("adaptability", ("Moslashuvchanlik", "Адаптивность", "Adaptability"),
     "soft-skills", ["flexibility"], [("PERSONAL_ETHICAL", 0.9), ("HEALTH", 0.4)]),
    ("emotional-intelligence", ("Emotsional intellekt", "Эмоциональный интеллект",
     "Emotional intelligence"), "soft-skills", ["eq"],
     [("SOCIAL", 1.0), ("PERSONAL_ETHICAL", 0.8)]),
    ("english", ("Ingliz tili", "Английский язык", "English"), "languages", ["eng"],
     [("KNOWLEDGE", 0.9), ("PROFESSIONAL", 0.6)]),
    ("russian", ("Rus tili", "Русский язык", "Russian"), "languages", ["rus"],
     [("KNOWLEDGE", 0.7)]),
    ("uzbek", ("O'zbek tili", "Узбекский язык", "Uzbek"), "languages", [],
     [("KNOWLEDGE", 0.6)]),
    ("legal-literacy", ("Huquqiy savodxonlik", "Правовая грамотность", "Legal literacy"),
     "civic", [], [("CIVIC", 1.0)]),
    ("volunteering", ("Volontyorlik", "Волонтёрство", "Volunteering"), "civic", [],
     [("CIVIC", 1.0), ("SOCIAL", 0.7)]),
    ("healthy-habits", ("Sog'lom odatlar", "Здоровые привычки", "Healthy habits"),
     "wellbeing", [], [("HEALTH", 1.0)]),
    ("stress-management", ("Stressni boshqarish", "Управление стрессом", "Stress management"),
     "wellbeing", [], [("HEALTH", 1.0), ("PERSONAL_ETHICAL", 0.5)]),
]

DIMENSIONS = [
    (CapitalDimensionSlug.KNOWLEDGE, ("Bilim kapitali", "Капитал знаний", "Knowledge capital"),
     "#2563eb", "book", 1),
    (CapitalDimensionSlug.PROFESSIONAL, ("Kasbiy kapital", "Профессиональный капитал", "Professional capital"),
     "#0891b2", "briefcase", 2),
    (CapitalDimensionSlug.DIGITAL_AI, ("Raqamli va AI kapitali", "Цифровой и AI капитал", "Digital & AI capital"),
     "#7c3aed", "cpu", 3),
    (CapitalDimensionSlug.SOCIAL, ("Ijtimoiy kapital", "Социальный капитал", "Social capital"),
     "#db2777", "users", 4),
    (CapitalDimensionSlug.ENTREPRENEURIAL, ("Tadbirkorlik kapitali", "Предпринимательский капитал", "Entrepreneurial capital"),
     "#ea580c", "rocket", 5),
    (CapitalDimensionSlug.FINANCIAL, ("Moliyaviy kapital", "Финансовый капитал", "Financial capital"),
     "#16a34a", "coins", 6),
    (CapitalDimensionSlug.PERSONAL_ETHICAL, ("Shaxsiy va axloqiy kapital", "Личный и этический капитал", "Personal & ethical capital"),
     "#ca8a04", "compass", 7),
    (CapitalDimensionSlug.HEALTH, ("Sog'lom hayot kapitali", "Капитал здоровья", "Health capital"),
     "#dc2626", "heart", 8),
    (CapitalDimensionSlug.CIVIC, ("Fuqarolik kapitali", "Гражданский капитал", "Civic capital"),
     "#0d9488", "flag", 9),
]

# slug, names, category, demand, salary, [(skill, requirement, min_level)]
PROFESSIONS = [
    ("software-developer", ("Dasturchi", "Разработчик ПО", "Software Developer"), "programming",
     DemandLevel.HIGH, 12_000_000, [
         ("python", "REQUIRED", 65), ("git", "REQUIRED", 55), ("sql", "REQUIRED", 55),
         ("rest-api", "REQUIRED", 60), ("problem-solving", "REQUIRED", 60),
         ("english", "PREFERRED", 50), ("django", "PREFERRED", 55),
         ("teamwork", "PREFERRED", 50),
     ]),
    ("frontend-developer", ("Frontend dasturchi", "Frontend-разработчик", "Frontend Developer"), "programming",
     DemandLevel.HIGH, 10_000_000, [
         ("javascript", "REQUIRED", 65), ("html-css", "REQUIRED", 65), ("react", "REQUIRED", 60),
         ("git", "REQUIRED", 50), ("typescript", "PREFERRED", 55),
         ("ui-design", "PREFERRED", 45), ("english", "PREFERRED", 50),
     ]),
    ("data-analyst", ("Ma'lumotlar tahlilchisi", "Аналитик данных", "Data Analyst"), "data",
     DemandLevel.HIGH, 11_000_000, [
         ("sql", "REQUIRED", 70), ("excel", "REQUIRED", 60), ("statistics", "REQUIRED", 60),
         ("data-analysis", "REQUIRED", 65), ("power-bi", "REQUIRED", 55),
         ("python", "PREFERRED", 50), ("critical-thinking", "PREFERRED", 55),
         ("english", "PREFERRED", 50),
     ]),
    ("cybersecurity-specialist", ("Kiberxavfsizlik mutaxassisi", "Специалист по кибербезопасности", "Cybersecurity Specialist"),
     "cybersecurity", DemandLevel.HIGH, 14_000_000, [
         ("linux", "REQUIRED", 65), ("networking", "REQUIRED", 65), ("web-security", "REQUIRED", 60),
         ("siem", "REQUIRED", 55), ("incident-response", "PREFERRED", 50),
         ("python", "PREFERRED", 45), ("english", "PREFERRED", 55),
     ]),
    ("ux-ui-designer", ("UX/UI dizayner", "UX/UI дизайнер", "UX/UI Designer"), "design",
     DemandLevel.MEDIUM, 9_000_000, [
         ("figma", "REQUIRED", 65), ("ui-design", "REQUIRED", 65), ("ux-research", "REQUIRED", 55),
         ("prototyping", "REQUIRED", 55), ("communication", "PREFERRED", 55),
         ("html-css", "PREFERRED", 40),
     ]),
    ("digital-marketer", ("Raqamli marketolog", "Digital-маркетолог", "Digital Marketer"), "marketing",
     DemandLevel.MEDIUM, 8_000_000, [
         ("smm", "REQUIRED", 60), ("content-marketing", "REQUIRED", 60),
         ("analytics-marketing", "REQUIRED", 55), ("seo", "PREFERRED", 50),
         ("communication", "PREFERRED", 60),
     ]),
    ("accountant", ("Buxgalter", "Бухгалтер", "Accountant"), "finance",
     DemandLevel.MEDIUM, 7_500_000, [
         ("accounting", "REQUIRED", 70), ("excel", "REQUIRED", 60),
         ("financial-literacy", "REQUIRED", 65), ("legal-literacy", "PREFERRED", 45),
     ]),
    ("entrepreneur", ("Tadbirkor", "Предприниматель", "Entrepreneur"), "business",
     DemandLevel.MEDIUM, None, [
         ("entrepreneurship", "REQUIRED", 60), ("business-model", "REQUIRED", 60),
         ("sales", "REQUIRED", 55), ("financial-literacy", "REQUIRED", 55),
         ("leadership", "PREFERRED", 55), ("communication", "PREFERRED", 60),
     ]),
]


class Command(BaseCommand):
    help = "Seed regions, skills, capital dimensions, professions and default configs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing taxonomy first (fails if anything references it).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            self.stdout.write("Removing existing taxonomy...")
            SkillDimension.objects.all().delete()
            ProfessionSkill.objects.all().delete()
            Profession.objects.all().delete()
            Skill.objects.all().delete()
            SkillCategory.objects.all().delete()
            CapitalDimension.objects.all().delete()
            Region.objects.all().delete()

        self._seed_regions()
        categories = self._seed_categories()
        dimensions = self._seed_dimensions()
        skills = self._seed_skills(categories, dimensions)
        self._seed_professions(categories, skills)
        self._seed_configs()

        self.stdout.write(
            self.style.SUCCESS(
                f"Taxonomy ready: {Region.objects.count()} regions, "
                f"{SkillCategory.objects.count()} categories, "
                f"{Skill.objects.count()} skills, "
                f"{CapitalDimension.objects.count()} capital dimensions, "
                f"{Profession.objects.count()} professions."
            )
        )

    def _seed_regions(self):
        for code, uz, ru, en in REGIONS:
            Region.objects.update_or_create(
                code=code,
                defaults={"name_uz": uz, "name_ru": ru, "name_en": en, "is_active": True},
            )

    def _seed_categories(self) -> dict:
        created: dict[str, SkillCategory] = {}
        # Two passes so a child never looks for a parent that does not exist yet.
        for slug, (uz, ru, en), parent in CATEGORIES:
            category, _ = SkillCategory.objects.update_or_create(
                slug=slug,
                defaults={
                    "name_uz": uz,
                    "name_ru": ru,
                    "name_en": en,
                    "is_active": True,
                    "is_soft_skill": slug in SOFT_SKILL_CATEGORIES,
                },
            )
            created[slug] = category
        for order, (slug, _names, parent) in enumerate(CATEGORIES):
            if parent:
                created[slug].parent = created[parent]
            created[slug].order = order
            created[slug].save(update_fields=["parent", "order"])
        return created

    def _seed_dimensions(self) -> dict:
        created = {}
        for slug, (uz, ru, en), color, icon, order in DIMENSIONS:
            dimension, _ = CapitalDimension.objects.update_or_create(
                slug=slug,
                defaults={
                    "name_uz": uz,
                    "name_ru": ru,
                    "name_en": en,
                    "color": color,
                    "icon": icon,
                    "order": order,
                },
            )
            created[slug] = dimension
        return created

    def _seed_skills(self, categories, dimensions) -> dict:
        created = {}
        for slug, (uz, ru, en), category_slug, aliases, dimension_links in SKILLS:
            skill, _ = Skill.objects.update_or_create(
                slug=slug,
                defaults={
                    "name_uz": uz,
                    "name_ru": ru,
                    "name_en": en,
                    "category": categories[category_slug],
                    "aliases": aliases,
                    "is_active": True,
                },
            )
            created[slug] = skill
            for dimension_slug, weight in dimension_links:
                SkillDimension.objects.update_or_create(
                    skill=skill,
                    dimension=dimensions[dimension_slug],
                    defaults={"weight": weight},
                )
        return created

    def _seed_professions(self, categories, skills):
        for slug, (uz, ru, en), category_slug, demand, salary, skill_links in PROFESSIONS:
            profession, _ = Profession.objects.update_or_create(
                slug=slug,
                defaults={
                    "name_uz": uz,
                    "name_ru": ru,
                    "name_en": en,
                    "category": categories.get(category_slug),
                    "demand_level": demand,
                    "median_salary": salary,
                    "is_active": True,
                },
            )
            for order, (skill_slug, requirement, min_level) in enumerate(skill_links):
                ProfessionSkill.objects.update_or_create(
                    profession=profession,
                    skill=skills[skill_slug],
                    defaults={
                        "requirement": requirement,
                        "min_proficiency": min_level,
                        "order": order,
                    },
                )

    def _seed_configs(self):
        """Default scoring configuration — all of it tunable later (TZ §21)."""
        KnowledgeConfig.active()
        CapitalWeightConfig.active()
        MatchWeightProfile.active()

        MatchWeightProfile.objects.update_or_create(
            name="legacy_prompt_v1",
            defaults={
                "weights": LEGACY_PROMPT_WEIGHTS,
                "is_active": False,
                "notes": (
                    "The weighting from the original brief, kept for comparison. "
                    "Not active: it double-counts test and course results, which "
                    "already sit inside the knowledge score (docs/01-ANALYSIS.md §3.1)."
                ),
            },
        )
        MatchWeightProfile.objects.filter(name="default").update(weights=DEFAULT_WEIGHTS)
