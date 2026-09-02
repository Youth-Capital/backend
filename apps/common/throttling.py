"""Named rate limits.

`ScopedRateThrottle` needs `view.throttle_scope`, which a ViewSet action cannot
supply — DRF rejects unknown `as_view()` kwargs. Subclassing `UserRateThrottle`
with the scope baked in works on both plain views and viewset actions, and the
limit is visible in the class name at the call site.
"""

from rest_framework.throttling import UserRateThrottle


class CandidateSearchThrottle(UserRateThrottle):
    """Talent search reads other people's profiles — worth a tighter limit
    than ordinary browsing, both for load and for scraping."""

    scope = "candidate_search"


class AIThrottle(UserRateThrottle):
    """AI calls can cost money once a hosted provider is configured."""

    scope = "ai"
