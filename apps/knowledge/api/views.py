"""Knowledge analytics endpoints (prompt §4)."""

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.taxonomy.models import Skill

from ..services import get_knowledge_history, get_knowledge_overview


@extend_schema(tags=["knowledge"])
class KnowledgeOverviewView(APIView):
    """Radar / progress-bar payload for the Knowledge Analytics panel."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 12)), 50)
        return Response(get_knowledge_overview(request.user, limit=limit))


@extend_schema(tags=["knowledge"])
class KnowledgeHistoryView(APIView):
    """Score over time, for the trend chart."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        skill_id = request.query_params.get("skill")
        days = min(int(request.query_params.get("days", 180)), 730)
        skill = Skill.objects.filter(id=skill_id).first() if skill_id else None
        return Response(get_knowledge_history(request.user, skill=skill, days=days))


@extend_schema(tags=["knowledge"])
class KnowledgeRecomputeView(APIView):
    """Manual refresh — useful after a bulk import or from the admin panel."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.common.recompute import recompute_for_user

        return Response(recompute_for_user(request.user, reason="manual"))
