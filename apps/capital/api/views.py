"""Capital index endpoints (TZ §6)."""

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..services import get_capital_history, get_capital_overview, recompute_capital_index


@extend_schema(tags=["capital"])
class CapitalOverviewView(APIView):
    """Nine-axis radar for the personal cabinet."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(get_capital_overview(request.user))


@extend_schema(tags=["capital"])
class CapitalHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        days = min(int(request.query_params.get("days", 180)), 730)
        return Response(get_capital_history(request.user, days=days))


@extend_schema(tags=["capital"])
class CapitalRecomputeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        indexes = recompute_capital_index(request.user)
        return Response({"recomputed": len(indexes)})
