"""Admin analytics endpoints — the manager panel of TZ §14."""

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsAdmin

from ..services import (
    get_cohort_breakdown,
    get_funnel,
    get_outcome_metrics,
    get_platform_overview,
    get_risk_list,
    get_skill_demand,
    track,
)


@extend_schema(tags=["analytics"])
class AdminDashboardView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response(
            {
                "overview": get_platform_overview(),
                "funnel": get_funnel(),
                "outcomes": get_outcome_metrics(),
                "skill_demand": get_skill_demand(limit=10),
            }
        )


@extend_schema(tags=["analytics"])
class FunnelView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response(get_funnel())


@extend_schema(tags=["analytics"])
class CohortView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        dimension = request.query_params.get("dimension", "region")
        return Response(
            {"dimension": dimension, "rows": get_cohort_breakdown(dimension)}
        )


@extend_schema(tags=["analytics"])
class RiskListView(APIView):
    """Students drifting away from their plan (TZ §14 "Risk list")."""

    permission_classes = [IsAdmin]

    def get(self, request):
        days = min(int(request.query_params.get("inactive_days", 14)), 365)
        limit = min(int(request.query_params.get("limit", 50)), 200)
        return Response(get_risk_list(inactive_days=days, limit=limit))


@extend_schema(tags=["analytics"])
class SkillDemandView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 15)), 50)
        return Response(get_skill_demand(limit=limit))


@extend_schema(tags=["analytics"])
class OutcomeView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response(get_outcome_metrics())


@extend_schema(tags=["analytics"])
class TrackEventView(APIView):
    """Client-side event ingestion.

    Accepts only names from the fixed taxonomy — otherwise the funnel silently
    fills with typos.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from ..models import EventName

        name = request.data.get("name", "")
        if name not in EventName.values:
            return Response({"accepted": False, "reason": "unknown_event"}, status=400)

        track(
            request.user,
            name,
            request.data.get("properties") or {},
            session_id=request.data.get("session_id", ""),
        )
        return Response({"accepted": True})
