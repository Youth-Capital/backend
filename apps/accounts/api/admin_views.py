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
            "student_profile__region", "employer_profile"
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
    @extend_schema(responses={200: dict})
    @action(detail=True, methods=["get"])
    def overview(self, request, pk=None):
        """Everything the platform holds about one person, in one place.

        The list answers "who is on the platform"; this answers "who is this".
        An administrator handling a support request, a complaint or a
        verification should not have to open six screens and join them in their
        head.

        WHAT IS NOT HERE, AND CANNOT BE

        The password. Not because it is withheld — because it does not exist.
        What the account stores is an Argon2id hash: the password is run
        through a one-way function with a per-account salt, and the result is
        all that is kept. It can confirm a password typed at sign-in and it
        cannot produce one. The hash itself is deliberately not returned
        either: handed to anyone it becomes material for an offline guessing
        run against the one secret the account holder controls.

        An administrator who needs to get somebody back into their account
        issues a reset rather than reading a secret.

        LOOKING IS AN EVENT

        Opening this writes an audit entry. The platform holds the records of
        minors, and "who read this person's file, and when" is a question that
        has to have an answer. `PII_ACCESS` exists in the audit vocabulary for
        exactly this.
        """
        from apps.assessment.models import AttemptStatus, TestAttempt
        from apps.billing.models import Subscription
        from apps.experience.models import Experience
        from apps.learning.models import Certificate, Enrollment, EnrollmentStatus
        from apps.profiles.models import UserSkill

        from apps.audit.models import AuditLog

        from ..models import Consent, LoginDevice

        user = self.get_object()

        log_action(
            action=AuditAction.PII_ACCESS,
            obj=user,
            object_repr=user.email,
            actor=request.user,
            note="Administrator opened the full user record.",
            severity=AuditSeverity.NOTICE,
        )

        skills = list(
            UserSkill.objects.filter(user=user)
            .select_related("skill")
            .order_by("-proficiency")[:40]
        )
        enrollments = list(
            Enrollment.objects.filter(user=user).select_related("course")[:40]
        )
        attempts = list(
            TestAttempt.objects.filter(user=user).select_related("test").order_by(
                "-created_at"
            )[:20]
        )
        subscription = Subscription.objects.filter(user=user).select_related("plan").first()
        profile = getattr(user, "student_profile", None)
        company = getattr(user, "employer_profile", None)

        return Response(
            {
                "account": {
                    "id": str(user.id),
                    "email": user.email,
                    "email_verified": user.email_verified,
                    "phone": user.phone,
                    "role": user.role,
                    "display_name": user.display_name,
                    "preferred_language": user.preferred_language,
                    "is_active": user.is_active,
                    "is_superuser": user.is_superuser,
                    "date_joined": user.date_joined,
                    "last_login": user.last_login,
                    "last_login_ip": user.last_login_ip,
                    # Stated rather than omitted: an administrator looking for
                    # the password should find out here why there is none.
                    "password": "not stored — Argon2id hash only, cannot be read back",
                },
                "profile": (
                    {
                        "full_name": profile.full_name,
                        "youth_id": profile.youth_id,
                        "birth_date": profile.birth_date,
                        "age": profile.age,
                        "is_minor": profile.is_minor,
                        "region": profile.region.name if profile.region else None,
                        "city": profile.city,
                        "education_status": profile.education_status,
                        "employment_status": profile.employment_status,
                        "target_profession": (
                            profile.target_profession.name
                            if profile.target_profession_id
                            else None
                        ),
                        "profile_completion": profile.profile_completion,
                        "languages": profile.languages,
                        "avatar": profile.avatar.url if profile.avatar else None,
                    }
                    if profile
                    else None
                ),
                "company": (
                    {
                        "display_name": company.display_name,
                        "legal_name": company.legal_name,
                        "tax_id": company.tax_id,
                        "industry": company.industry,
                        "website": company.website,
                        "verification_status": company.verification_status,
                    }
                    if company
                    else None
                ),
                "skills": {
                    "total": len(skills),
                    "verified": sum(1 for s in skills if s.is_verified),
                    "items": [
                        {
                            "name": s.skill.name,
                            "proficiency": s.proficiency,
                            "verified": s.is_verified,
                            "band": s.band,
                        }
                        for s in skills
                    ],
                },
                "learning": {
                    "enrolled": len(enrollments),
                    "completed": sum(
                        1 for e in enrollments if e.status == EnrollmentStatus.COMPLETED
                    ),
                    "courses": [
                        {
                            "title": e.course.title,
                            "status": e.status,
                            "progress": e.progress,
                        }
                        for e in enrollments
                    ],
                    "certificates": [
                        {"title": c.course.title, "serial": c.serial, "issued_at": c.issued_at}
                        for c in Certificate.objects.filter(user=user).select_related(
                            "course"
                        )[:20]
                    ],
                },
                "assessment": {
                    "attempts": len(attempts),
                    "passed": sum(
                        1 for a in attempts if a.status == AttemptStatus.PASSED
                    ),
                    "items": [
                        {
                            "test": a.test.title,
                            "status": a.status,
                            "score": a.score,
                            "taken_at": a.created_at,
                        }
                        for a in attempts
                    ],
                },
                "experience": [
                    {
                        "title": e.title,
                        "organization": e.organization,
                        "type": e.type,
                        "months": e.duration_months,
                        "verification_status": e.verification_status,
                    }
                    for e in Experience.objects.filter(user=user)[:20]
                ],
                "billing": (
                    {
                        "plan": subscription.plan.code,
                        "status": subscription.status,
                        "period_start": subscription.current_period_start,
                        "period_end": subscription.current_period_end,
                    }
                    if subscription
                    else None
                ),
                "consents": [
                    {
                        "type": c.type,
                        "version": c.version,
                        "granted": c.granted,
                        "granted_at": c.granted_at,
                        "revoked_at": c.revoked_at,
                    }
                    for c in Consent.objects.filter(user=user)
                ],
                "devices": [
                    {
                        "browser": d.browser,
                        "system": d.system,
                        "kind": d.kind,
                        "last_ip": d.last_ip,
                        "last_seen_at": d.last_seen_at,
                    }
                    for d in LoginDevice.objects.filter(user=user).order_by(
                        "-last_seen_at"
                    )[:10]
                ],
                "recent_activity": [
                    {
                        "action": entry.action,
                        "object_type": entry.object_type,
                        "object_repr": entry.object_repr,
                        "created_at": entry.created_at,
                        "ip": entry.ip,
                    }
                    for entry in AuditLog.objects.filter(actor=user).order_by(
                        "-created_at"
                    )[:20]
                ],
            }
        )

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
    """Approve or reject employer verification."""

    permission_classes = [IsAdmin]

    def list(self, request):
        from apps.common.enums import VerificationStatus
        from apps.profiles.models import EmployerProfile

        employers = EmployerProfile.objects.filter(
            verification_status=VerificationStatus.PENDING
        ).select_related("owner", "region")
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
