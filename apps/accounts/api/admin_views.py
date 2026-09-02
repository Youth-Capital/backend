"""Administrator user management (prompt §21).

Separate from the auth views: these endpoints are about *other* people's
accounts, so every one of them is admin-only and every mutation is audited.
"""

from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit.models import AuditAction, AuditSeverity
from apps.audit.services import log_action
from apps.common.enums import Role
from apps.common.exceptions import DomainError, NotAllowed
from apps.common.permissions import IsAdmin

from ..models import User


class AdminUserSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    profile_summary = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "phone",
            "role",
            "display_name",
            "preferred_language",
            "is_active",
            "email_verified",
            "date_joined",
            "last_login",
            "profile_summary",
        ]
        read_only_fields = ["id", "email", "date_joined", "last_login"]

    def get_profile_summary(self, user) -> dict:
        if user.role == Role.STUDENT:
            profile = getattr(user, "student_profile", None)
            if profile is None:
                return {}
            return {
                "youth_id": profile.youth_id,
                "region": profile.region.name if profile.region else None,
                "profile_completion": profile.profile_completion,
                "education_status": profile.education_status,
            }
        if user.role == Role.EMPLOYER:
            company = getattr(user, "employer_profile", None)
            if company is None:
                return {}
            return {
                "company": company.display_name,
                "verification_status": company.verification_status,
            }
        if user.role == Role.MENTOR:
            mentor = getattr(user, "mentor_profile", None)
            if mentor is None:
                return {}
            return {
                "headline": mentor.headline,
                "verification_status": mentor.verification_status,
                "sessions": mentor.sessions_count,
            }
        return {}


@extend_schema(tags=["admin"])
class AdminUserViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdmin]
    serializer_class = AdminUserSerializer
    filterset_fields = ["role", "is_active", "email_verified"]
    ordering_fields = ["date_joined", "last_login", "email"]
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_queryset(self):
        queryset = User.objects.select_related(
            "student_profile__region", "employer_profile", "mentor_profile"
        ).order_by("-date_joined")

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(email__icontains=search)
                | Q(student_profile__first_name__icontains=search)
                | Q(student_profile__last_name__icontains=search)
                | Q(student_profile__youth_id__icontains=search)
                | Q(employer_profile__brand_name__icontains=search)
            )
        return queryset

    def perform_update(self, serializer):
        from apps.audit.services import diff_fields

        before, after = diff_fields(serializer.instance, serializer.validated_data)
        user = serializer.save()
        if before:
            log_action(
                action=AuditAction.UPDATE,
                obj=user,
                actor=self.request.user,
                before=before,
                after=after,
                severity=AuditSeverity.NOTICE,
            )

    @extend_schema(request=None, responses={200: AdminUserSerializer})
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user = self.get_object()
        if user.id == request.user.id:
            raise NotAllowed(
                "You cannot deactivate your own account.", code="self_deactivate"
            )

        user.is_active = False
        user.save(update_fields=["is_active", "updated_at"])
        log_action(
            action=AuditAction.UPDATE,
            obj=user,
            actor=request.user,
            before={"is_active": True},
            after={"is_active": False},
            note="deactivated by admin",
            severity=AuditSeverity.WARNING,
        )
        return Response(AdminUserSerializer(user).data)

    @extend_schema(request=None, responses={200: AdminUserSerializer})
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        user = self.get_object()
        user.is_active = True
        user.save(update_fields=["is_active", "updated_at"])
        log_action(
            action=AuditAction.UPDATE,
            obj=user,
            actor=request.user,
            before={"is_active": False},
            after={"is_active": True},
            severity=AuditSeverity.NOTICE,
        )
        return Response(AdminUserSerializer(user).data)

    @extend_schema(request=dict, responses={200: AdminUserSerializer})
    @action(detail=True, methods=["post"], url_path="change-role")
    def change_role(self, request, pk=None):
        """Role changes are the highest-privilege action here, so they are
        logged at CRITICAL and refused on the acting admin's own account."""
        user = self.get_object()
        new_role = request.data.get("role")

        if new_role not in Role.values:
            raise DomainError("Unknown role.", code="unknown_role")
        if user.id == request.user.id:
            raise NotAllowed("You cannot change your own role.", code="self_role_change")

        previous = user.role
        user.role = new_role
        user.is_staff = new_role == Role.ADMIN
        user.save(update_fields=["role", "is_staff", "updated_at"])

        log_action(
            action=AuditAction.ROLE_CHANGE,
            obj=user,
            actor=request.user,
            before={"role": previous},
            after={"role": new_role},
            severity=AuditSeverity.CRITICAL,
        )
        return Response(AdminUserSerializer(user).data)


@extend_schema(tags=["admin"])
class EmployerVerificationViewSet(viewsets.ViewSet):
    """Approve or reject employer and mentor verification."""

    permission_classes = [IsAdmin]

    def list(self, request):
        from apps.common.enums import VerificationStatus
        from apps.profiles.models import EmployerProfile, MentorProfile

        employers = EmployerProfile.objects.filter(
            verification_status=VerificationStatus.PENDING
        ).select_related("owner", "region")
        mentors = MentorProfile.objects.filter(
            verification_status=VerificationStatus.PENDING
        ).select_related("user")

        return Response(
            {
                "employers": [
                    {
                        "id": str(company.id),
                        "name": company.display_name,
                        "legal_name": company.legal_name,
                        "tax_id": company.tax_id,
                        "industry": company.industry,
                        "email": company.owner.email,
                        "created_at": company.created_at,
                    }
                    for company in employers
                ],
                "mentors": [
                    {
                        "id": str(mentor.id),
                        "name": mentor.full_name,
                        "headline": mentor.headline,
                        "email": mentor.user.email,
                        "years_experience": mentor.years_experience,
                        "created_at": mentor.created_at,
                    }
                    for mentor in mentors
                ],
            }
        )

    @action(detail=False, methods=["post"], url_path="employer")
    def verify_employer(self, request):
        from django.utils import timezone

        from apps.common.enums import VerificationStatus
        from apps.profiles.models import EmployerProfile

        company = EmployerProfile.objects.filter(id=request.data.get("id")).first()
        if company is None:
            raise DomainError("Unknown company.", code="not_found")

        approve = bool(request.data.get("approve"))
        company.verification_status = (
            VerificationStatus.VERIFIED if approve else VerificationStatus.REJECTED
        )
        company.verified_at = timezone.now() if approve else None
        company.verified_by = request.user
        company.save(
            update_fields=[
                "verification_status",
                "verified_at",
                "verified_by",
                "updated_at",
            ]
        )
        log_action(
            action=AuditAction.MODERATE,
            obj=company,
            actor=request.user,
            after={"verification_status": company.verification_status},
            severity=AuditSeverity.NOTICE,
        )
        return Response({"verification_status": company.verification_status})

    @action(detail=False, methods=["post"], url_path="mentor")
    def verify_mentor(self, request):
        from apps.common.enums import VerificationStatus
        from apps.profiles.models import MentorProfile

        mentor = MentorProfile.objects.filter(id=request.data.get("id")).first()
        if mentor is None:
            raise DomainError("Unknown mentor.", code="not_found")

        approve = bool(request.data.get("approve"))
        mentor.verification_status = (
            VerificationStatus.VERIFIED if approve else VerificationStatus.REJECTED
        )
        mentor.save(update_fields=["verification_status", "updated_at"])
        log_action(
            action=AuditAction.MODERATE,
            obj=mentor,
            actor=request.user,
            after={"verification_status": mentor.verification_status},
            severity=AuditSeverity.NOTICE,
        )
        return Response({"verification_status": mentor.verification_status})
