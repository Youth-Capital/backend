"""Access control as machinery, not as a habit.

Two access bugs reached production-shaped code here, and both had the same
cause: authorisation lived in whatever each view's `get_queryset` happened to
say, so security held only for as long as every author of every viewset
remembered to write the filter. One forgot it entirely (`LessonViewSet`
returned every lesson on the platform); one wrote it in a way that inverted
under a null (`QuestionViewSet` handed learners the answer key). Neither is a
lapse of care — both files are otherwise careful. They are what happens when
the safe thing has to be remembered rather than declared.

So the rules move here, and the parts that were easy to get wrong become
things a view cannot express by accident:

* `company_owned()` — a company filter that matches nothing when the account
  has no company, instead of matching everything the platform owns.
* `AccessPolicy` — every API view states, in one line, who its rows belong to.
  A contract test walks the URL map and fails on any view that has not said.
* `ScopedModelViewSet` — a base whose default is to return nothing. A subclass
  that forgets to scope gets an empty list, not the whole table.

None of this replaces the per-view rules; it replaces the *silence* where a
rule was supposed to be.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q
from rest_framework import viewsets


# ---------------------------------------------------------------------------
# Null-safe ownership filters
# ---------------------------------------------------------------------------
def company_of(user):
    """The employer profile behind this account, or None."""
    return getattr(user, "employer_profile", None)


def company_owned(user, field: str = "employer") -> Q:
    """Rows owned by this user's company — and nothing at all without one.

    `Q(employer=company)` with `company = None` does not mean "no rows". It
    compiles to `employer IS NULL`, and in this schema a null employer is how a
    *platform-owned* row looks. So the filter meant to narrow to "mine" widens
    to "everything the platform owns" for exactly the accounts that should see
    least: a learner, or an employer whose company profile does not exist yet.

    `Q(pk__in=[])` is the empty set and still composes with `|`, so callers can
    keep writing `Q(author=user) | company_owned(user, "test__employer")` and
    get the reading they intended.
    """
    company = company_of(user)
    if company is None:
        return Q(pk__in=[])
    return Q(**{field: company})


def owns_company_object(user, obj, field: str = "employer_id") -> bool:
    """Whether this account's company owns `obj`, null-safely."""
    company = company_of(user)
    return bool(company and getattr(obj, field, None) == company.id)


# ---------------------------------------------------------------------------
# Declared policy
# ---------------------------------------------------------------------------
#: Anyone may read these rows, including signed-out visitors. Reference data
#: and public catalogue only — never anything belonging to a person.
PUBLIC = "public"
#: Rows belong to one account and only that account may see them.
OWNER = "owner"
#: Visibility depends on the role: a learner sees published rows, an employer
#: sees their company's, an admin sees all. The view carries the rule.
ROLE_SCOPED = "role_scoped"
#: Staff only.
ADMIN = "admin"
#: Not a collection of rows at all — an action, a dashboard, a health check.
NON_COLLECTION = "non_collection"

POLICIES = frozenset({PUBLIC, OWNER, ROLE_SCOPED, ADMIN, NON_COLLECTION})


@dataclass(frozen=True)
class AccessPolicy:
    """Who may see the rows behind a view, said out loud.

    `reason` is required for `PUBLIC`, because "everyone may read this" is the
    one answer that should never be arrived at by default.
    """

    kind: str
    reason: str = ""

    def __post_init__(self):
        if self.kind not in POLICIES:
            raise ValueError(f"Unknown access policy: {self.kind!r}")
        if self.kind == PUBLIC and not self.reason:
            raise ValueError("A public view must say why it is public.")


def policy(kind: str, reason: str = ""):
    """Class decorator: `@policy(OWNER)` on an API view."""

    def apply(view_class):
        view_class.access_policy = AccessPolicy(kind, reason)
        return view_class

    return apply


# ---------------------------------------------------------------------------
# Deny by default
# ---------------------------------------------------------------------------
class ScopedQuerysetMixin:
    """A queryset that starts empty and has to be opened deliberately.

    Subclasses implement `scope_queryset(queryset)` instead of `get_queryset`.
    The difference matters: overriding `get_queryset` and returning the model's
    full manager is one forgotten line away from every row in the table, while
    forgetting `scope_queryset` returns nothing and is noticed the first time
    the page is opened.
    """

    #: The unscoped starting point, e.g. `Lesson.objects.all()`.
    base_queryset = None

    def get_base_queryset(self):
        if self.base_queryset is None:
            raise NotImplementedError(
                f"{type(self).__name__} must set `base_queryset`."
            )
        return self.base_queryset.all()

    def scope_queryset(self, queryset):
        """Narrow to what `self.request.user` may see. Deny by default."""
        return queryset.none()

    def get_queryset(self):
        return self.scope_queryset(self.get_base_queryset())


class ScopedModelViewSet(ScopedQuerysetMixin, viewsets.ModelViewSet):
    pass


class ScopedReadOnlyModelViewSet(ScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    pass
