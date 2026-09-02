"""DRF permission that reads the entitlement gate.

Use this for read-shaped capabilities where merely reaching the endpoint is
the thing being sold. For anything that *consumes* quota, call
`services.consume()` inside the view instead — a permission runs before the
action and cannot know whether the work actually happened, so metering there
would bill users for requests that later failed.
"""

from rest_framework.permissions import BasePermission

from .services import check_feature


class HasFeature(BasePermission):
    """Set `required_feature` on the view.

    A view without one is allowed through: forgetting to declare a feature
    should not silently paywall an endpoint that was never meant to be paid.
    """

    message = "This feature is not available on your current plan."

    def has_permission(self, request, view) -> bool:
        feature = getattr(view, "required_feature", None)
        if not feature:
            return True

        user = request.user
        if not (user and user.is_authenticated):
            return False

        decision = check_feature(user, feature)
        if not decision.allowed:
            self.message = {
                "code": decision.reason,
                "feature": decision.feature,
                "limit": decision.limit,
                "used": decision.used,
                "plan": decision.plan_code,
            }
        return decision.allowed
