"""Access control has to be a property of the codebase, not of a habit.

Two real holes here had the same shape. `LessonViewSet.get_queryset` returned
`Lesson.objects.select_related("module__course")` — every lesson on the
platform — and `QuestionViewSet` filtered on a company that is None for
learners, which inverted into "everything the platform owns". Both files are
careful in every other respect. Nobody was careless; the safe thing simply had
to be *remembered*, once per viewset, forever.

So these tests read the API back and insist that each row-returning view has
made a decision about who its rows belong to. They do not check that the
decision is right — a test cannot know that. They check that one was made, and
that the two specific ways of getting it wrong that already happened here
cannot happen quietly again.

Adding a viewset that does not scope its rows fails the suite. That is the
point: the next person does not have to know this history.
"""

import inspect
import re
from pathlib import Path

from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.viewsets import ViewSetMixin

# ---------------------------------------------------------------------------
# Reviewed exceptions. Each one names why it is allowed to skip scoping.
# ---------------------------------------------------------------------------
#: Staff-only surfaces: seeing everything is the job.
ADMIN_SURFACES = {
    "AIProviderConfigViewSet",
    "AIRequestLogViewSet",
    "AuditLogViewSet",
    "ReviewCampaignViewSet",
    "MatchWeightProfileViewSet",
    "AdminUserViewSet",
    "EmployerVerificationViewSet",
    "SafetyEventViewSet",
    "ReviewAdminViewSet",
}

#: Shared reference data — regions, skills, professions. Not personal, and the
#: product is unusable if a learner cannot read the skill list.
PUBLIC_REFERENCE = {
    "RegionViewSet",
    "ProfessionViewSet",
    "SkillViewSet",
    "SkillCategoryViewSet",
    "CapitalDimensionViewSet",
    "SkillDimensionViewSet",
}

#: Row-returning views whose scoping lives in an explicit per-action check
#: rather than in the queryset. Allowed, named, and each one says where.
CHECKED_PER_ACTION = {
    # `list` and `retrieve` both call can_open_course(); the queryset narrows
    # to openable courses as well, so this is belt and braces.
    "CourseMaterialViewSet": "can_open_course in list/retrieve",
}

EXEMPT = ADMIN_SURFACES | PUBLIC_REFERENCE | set(CHECKED_PER_ACTION)

#: What counts as "this view thought about who is asking".
SCOPING_SIGNALS = (
    "request.user",
    "company_owned",
    "scope_queryset",
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _api_view_classes():
    """Every view class reachable under /api/v1/, once each."""

    def walk(resolver, prefix=""):
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                yield from walk(entry, prefix + str(entry.pattern))
            elif isinstance(entry, URLPattern):
                yield prefix + str(entry.pattern), entry.callback

    found = {}
    for route, callback in walk(get_resolver()):
        if not route.startswith("api/v1/"):
            continue
        cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if cls is None or not cls.__module__.startswith("apps."):
            continue
        found[cls.__name__] = cls
    return found


def _is_admin_only(cls) -> bool:
    return any(
        permission.__name__ in {"IsAdmin", "IsAdminUser"}
        for permission in getattr(cls, "permission_classes", [])
    )


# ---------------------------------------------------------------------------
# 1. Every collection says who its rows belong to
# ---------------------------------------------------------------------------
def test_every_viewset_scopes_its_rows_or_is_a_named_exception():
    offenders = []

    for name, cls in sorted(_api_view_classes().items()):
        if not issubclass(cls, ViewSetMixin):
            continue
        if name in EXEMPT or _is_admin_only(cls):
            continue

        get_queryset = getattr(cls, "get_queryset", None)
        if get_queryset is None or "get_queryset" not in vars(cls):
            offenders.append(f"{name}: no get_queryset of its own")
            continue

        source = inspect.getsource(get_queryset)
        if not any(signal in source for signal in SCOPING_SIGNALS):
            offenders.append(
                f"{name}: get_queryset never mentions who is asking"
            )

    assert not offenders, (
        "These viewsets return rows without narrowing them to the caller.\n"
        "Scope the queryset, or add the class to a reviewed exception set in "
        "this file with the reason:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 2. The null-company footgun cannot come back
# ---------------------------------------------------------------------------
def test_no_view_filters_on_a_company_that_may_be_missing():
    """`Q(employer=company)` with company None means "platform-owned".

    This is not a style rule. It is the exact line that handed every learner
    the answer key: `getattr(user, "employer_profile", None)` returns None for
    anyone who is not an employer, and `employer=None` matches every row the
    platform owns rather than no rows at all. `company_owned()` returns an
    empty filter instead, so the only safe spelling is the short one.
    """
    pattern = re.compile(
        r"employer_profile\"?,\s*None\)(?P<body>(?:.|\n){0,600})", re.MULTILINE
    )
    filter_use = re.compile(r"(Q\(\s*\w*employer\w*\s*=\s*company|employer\s*=\s*company\b)")
    guard = re.compile(r"company\s+is\s+not\s+None|if\s+company\b|company\s+and\b")

    offenders = []
    for path in sorted((BACKEND_ROOT / "apps").rglob("api/views.py")):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            body = match.group("body")
            if filter_use.search(body) and not guard.search(body):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{line}")

    assert not offenders, (
        "A company filter is built from a value that can be None. Use "
        "apps.common.access.company_owned() instead:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 3. Writing is a decision of its own
# ---------------------------------------------------------------------------
WRITE_GUARDS = (
    "perform_create",
    "perform_update",
    "perform_destroy",
    "get_permissions",
    # Overriding the action itself is the other place a guard legitimately
    # lives: ApplicationViewSet.create refuses anyone who is not a student,
    # InterviewInviteViewSet.create anyone who is not the hiring employer.
    "create",
    "update",
    "destroy",
)

#: Writable viewsets where every row already belongs to the caller, so the
#: queryset is the write guard: you can only reach your own rows to change.
OWNER_WRITABLE = {
    "MyNoteViewSet",
    "MyEducationViewSet",
    "MySkillsViewSet",
    "NotificationPreferenceViewSet",
    "SavedVacancyViewSet",
    "GoalViewSet",
    "TaskViewSet",
    "CVViewSet",
    "PortfolioViewSet",
    "ExperienceViewSet",
}


def test_every_writable_viewset_guards_its_writes():
    """A create takes its ids from the body and never passes the queryset.

    This is how a learner came to be able to post an employment record naming
    any company: reads were scoped, writes were not, and nothing in between
    said so.
    """
    offenders = []

    for name, cls in sorted(_api_view_classes().items()):
        if not issubclass(cls, ViewSetMixin):
            continue
        if name in ADMIN_SURFACES or name in PUBLIC_REFERENCE or _is_admin_only(cls):
            continue
        if name in OWNER_WRITABLE:
            continue

        actions = set(getattr(cls, "http_method_names", []))
        writes = {"post", "put", "patch", "delete"} & actions
        if not writes:
            continue
        # A ReadOnlyModelViewSet exposes no write actions at all.
        if not hasattr(cls, "create") and not hasattr(cls, "update"):
            continue

        if not any(guard in vars(cls) for guard in WRITE_GUARDS):
            offenders.append(name)

    assert not offenders, (
        "These viewsets accept writes without a guard on who may write, and a "
        "write never passes through get_queryset:\n  " + "\n  ".join(offenders)
    )
