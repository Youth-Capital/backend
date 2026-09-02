"""Edu-Job endpoints: vacancies, applications, candidates."""

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.enums import ModerationStatus, Role
from apps.common.exceptions import DomainError, NotAllowed
from apps.common.permissions import IsAdmin, IsEmployer, IsStudent
from apps.common.throttling import CandidateSearchThrottle

from ..models import (
    Application,
    Interview,
    Placement,
    SavedVacancy,
    Vacancy,
    VacancySkill,
)
from ..services import (
    apply_to_vacancy,
    change_application_status,
    close_vacancy,
    moderate_vacancy,
    rank_candidates,
    submit_vacancy_for_review,
    withdraw_application,
)
from .serializers import (
    ApplicationSerializer,
    CreateApplicationSerializer,
    EmployerApplicationSerializer,
    InterviewInviteSerializer,
    InterviewSerializer,
    PlacementSerializer,
    SavedVacancySerializer,
    VacancyDetailSerializer,
    VacancyListSerializer,
    VacancySkillSerializer,
    VacancyWriteSerializer,
)


@extend_schema(tags=["jobs"])
class VacancyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    filterset_fields = [
        "employment_type",
        "work_mode",
        "region",
        "profession",
        "education_required",
        "language",
    ]
    search_fields = ["title", "description", "employer__brand_name", "employer__legal_name"]
    ordering_fields = ["published_at", "salary_min", "deadline"]

    def get_queryset(self):
        user = self.request.user
        base = Vacancy.objects.select_related(
            "employer", "region", "profession"
        ).prefetch_related("skill_links__skill__category")

        if user.is_admin:
            queryset = base
        elif user.role == Role.EMPLOYER:
            company = getattr(user, "employer_profile", None)
            if company is None:
                # No company attached yet: nothing of their own to show, and
                # the public board is not the safe default here.
                queryset = base.none()
            elif self.request.query_params.get("scope") == "board":
                # Deliberate opt-in to the public listing.
                queryset = base.filter(
                    Q(status=ModerationStatus.PUBLISHED) | Q(employer=company)
                )
            else:
                # A company sees its own vacancies by default.
                #
                # This used to be "published OR mine", which meant the
                # employer's own vacancy page listed every published vacancy in
                # the country. Clicking one of them opened another company's
                # candidate list and hit the ownership check — a 403 for a row
                # the product had just invited them to click. Ownership was
                # never actually breached; the list simply had no business
                # offering the row.
                queryset = base.filter(employer=company)
        else:
            queryset = base.filter(status=ModerationStatus.PUBLISHED)

        if self.request.query_params.get("open_only") == "true":
            today = timezone.localdate()
            queryset = queryset.filter(
                Q(deadline__isnull=True) | Q(deadline__gte=today)
            )

        skill = self.request.query_params.get("skill")
        if skill:
            queryset = queryset.filter(skill_links__skill_id=skill)

        moderation_status = self.request.query_params.get("status")
        if moderation_status and user.is_admin:
            queryset = queryset.filter(status=moderation_status)

        min_match = self.request.query_params.get("min_match")
        if min_match and user.role == Role.STUDENT:
            from apps.matching.models import MatchResult

            good = MatchResult.objects.filter(
                student=user, overall_score__gte=int(min_match)
            ).values_list("vacancy_id", flat=True)
            queryset = queryset.filter(id__in=list(good))

        return queryset.distinct()

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return VacancyWriteSerializer
        if self.action == "retrieve":
            return VacancyDetailSerializer
        return VacancyListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        user = self.request.user
        if user.is_authenticated and user.role == Role.STUDENT:
            from apps.matching.models import MatchResult

            context["matches_by_vacancy"] = {
                m.vacancy_id: m for m in MatchResult.objects.filter(student=user)
            }
            context["applications_by_vacancy"] = {
                a.vacancy_id: a for a in Application.objects.filter(student=user)
            }
            context["saved_vacancy_ids"] = set(
                SavedVacancy.objects.filter(user=user).values_list(
                    "vacancy_id", flat=True
                )
            )
        return context

    def perform_create(self, serializer):
        user = self.request.user
        company = getattr(user, "employer_profile", None)
        if company is None and not user.is_admin:
            raise NotAllowed("Only employers can post vacancies.")
        serializer.save(employer=company)

    def _assert_owner(self, vacancy: Vacancy) -> None:
        user = self.request.user
        if user.is_admin:
            return
        company = getattr(user, "employer_profile", None)
        if company and vacancy.employer_id == company.id:
            return
        raise NotAllowed("This vacancy belongs to another company.")

    def perform_update(self, serializer):
        self._assert_owner(self.get_object())
        serializer.save()

    def retrieve(self, request, *args, **kwargs):
        vacancy = self.get_object()
        if request.user.role == Role.STUDENT:
            Vacancy.objects.filter(pk=vacancy.pk).update(
                views_count=vacancy.views_count + 1
            )
            from apps.analytics.services import track

            track(request.user, "vacancy_viewed", {"vacancy_id": str(vacancy.id)})
        return Response(
            VacancyDetailSerializer(vacancy, context=self.get_serializer_context()).data
        )

    # -- lifecycle -------------------------------------------------------
    @extend_schema(request=None, responses={200: VacancyDetailSerializer})
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        vacancy = self.get_object()
        self._assert_owner(vacancy)
        # Quota is checked here, not at moderation: the employer holds the
        # plan, so the employer is who must be told the slot is full.
        from apps.billing.enums import Feature as BillingFeature
        from apps.billing.services import require_feature

        require_feature(request.user, BillingFeature.ACTIVE_VACANCY)

        vacancy = submit_vacancy_for_review(vacancy, actor=request.user)
        return Response(
            VacancyDetailSerializer(vacancy, context=self.get_serializer_context()).data
        )

    @extend_schema(request=dict, responses={200: VacancyDetailSerializer})
    @action(detail=True, methods=["post"], permission_classes=[IsAdmin])
    def moderate(self, request, pk=None):
        vacancy = moderate_vacancy(
            self.get_object(),
            approve=bool(request.data.get("approve")),
            actor=request.user,
            note=request.data.get("note", ""),
        )
        return Response(
            VacancyDetailSerializer(vacancy, context=self.get_serializer_context()).data
        )

    @extend_schema(request=None, responses={200: VacancyDetailSerializer})
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        vacancy = self.get_object()
        self._assert_owner(vacancy)
        vacancy = close_vacancy(vacancy, actor=request.user)
        return Response(
            VacancyDetailSerializer(vacancy, context=self.get_serializer_context()).data
        )

    @extend_schema(request=VacancySkillSerializer, responses={201: VacancySkillSerializer})
    @action(detail=True, methods=["post"], url_path="skills")
    def add_skill(self, request, pk=None):
        vacancy = self.get_object()
        self._assert_owner(vacancy)
        serializer = VacancySkillSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link, _created = VacancySkill.objects.update_or_create(
            vacancy=vacancy,
            skill=serializer.validated_data["skill"],
            defaults={
                "requirement": serializer.validated_data.get("requirement", "REQUIRED"),
                "min_knowledge_score": serializer.validated_data.get(
                    "min_knowledge_score", 50
                ),
                "weight": serializer.validated_data.get("weight", 1),
                "order": serializer.validated_data.get("order", 0),
            },
        )
        return Response(VacancySkillSerializer(link).data, status=201)

    @extend_schema(responses={204: None})
    @action(detail=True, methods=["delete"], url_path=r"skills/(?P<skill_id>[^/.]+)")
    def remove_skill(self, request, pk=None, skill_id=None):
        vacancy = self.get_object()
        self._assert_owner(vacancy)
        VacancySkill.objects.filter(vacancy=vacancy, skill_id=skill_id).delete()
        return Response(status=204)

    # -- candidates ------------------------------------------------------
    @extend_schema(responses={200: dict})
    @action(
        detail=True,
        methods=["get"],
        permission_classes=[IsAuthenticated],
        throttle_classes=[CandidateSearchThrottle],
    )
    def candidates(self, request, pk=None):
        """Ranked candidates with the "why" (prompt §18, §19).

        Metered: this is the expensive, sellable capability. Consumption is
        recorded only after ownership passes, so a rejected request does not
        burn the employer's quota.
        """
        vacancy = self.get_object()
        self._assert_owner(vacancy)

        from apps.billing.enums import Feature as BillingFeature
        from apps.billing.services import consume

        consume(request.user, BillingFeature.CANDIDATE_SEARCH)

        from apps.profiles.services import can_view_student_profile

        min_score = int(request.query_params.get("min_score", 0))
        limit = min(int(request.query_params.get("limit", 50)), 200)
        matches = rank_candidates(vacancy, min_score=min_score, limit=limit)

        applied = set(
            Application.objects.filter(vacancy=vacancy).values_list(
                "student_id", flat=True
            )
        )

        rows = []
        for match in matches:
            profile = getattr(match.student, "student_profile", None)
            identified = can_view_student_profile(
                viewer=request.user, student_user=match.student
            )
            rows.append(
                {
                    "user_id": str(match.student_id),
                    "youth_id": getattr(profile, "youth_id", None),
                    "name": profile.full_name if (identified and profile) else None,
                    "identified": identified,
                    "has_applied": match.student_id in applied,
                    "match": {
                        "overall": match.overall_score,
                        "coverage": match.coverage_score,
                        "knowledge": match.knowledge_score,
                        "verification": match.verification_score,
                        "experience": match.experience_score,
                        "education": match.education_score,
                        "location": match.location_score,
                    },
                    "skills": match.matched_skills,
                    "missing_skills": match.missing_skills,
                    "explanation": match.explanation,
                }
            )
        return Response({"count": len(rows), "results": rows})

    @extend_schema(responses={200: dict})
    @action(
        detail=True,
        methods=["get"],
        url_path=r"candidates/(?P<user_id>[0-9a-f-]{36})",
        permission_classes=[IsAuthenticated],
    )
    def candidate(self, request, pk=None, user_id=None):
        """One candidate, in full, for this vacancy.

        Two rules shape what comes back.

        *Reachability*: the person must already be a match for this vacancy.
        Without that check the URL would be a profile reader for any user id an
        employer cared to type, which is not what paying for a candidate search
        buys.

        *Identity*: the same rule as the list decides whether a name is shown.
        Everything a name would reveal travels with it — the photo, the free
        text they wrote about themselves, the college they attend, the
        employers on their record — because a "pseudonymised" card that names
        the school and the last employer is not pseudonymised. Capability is
        the part talent search is allowed to see, so skills, tests and the
        shape of their experience stay in both versions.

        Not metered: the search was paid for when the list was built, and
        charging again for reading one of its rows would meter curiosity.
        """
        from apps.assessment.models import AttemptStatus, TestAttempt
        from apps.audit.models import AuditAction, AuditSeverity
        from apps.audit.services import log_action
        from apps.experience.models import Experience
        from apps.learning.models import Certificate
        from apps.matching.models import MatchResult
        from apps.profiles.models import UserSkill
        from apps.profiles.services import can_view_student_profile

        vacancy = self.get_object()
        self._assert_owner(vacancy)

        match = (
            MatchResult.objects.filter(vacancy=vacancy, student_id=user_id)
            .select_related("student", "student__student_profile")
            .first()
        )
        if match is None:
            raise NotAllowed(
                "This person is not a candidate for this vacancy.", code="not_found"
            )

        student = match.student
        profile = getattr(student, "student_profile", None)
        identified = can_view_student_profile(viewer=request.user, student_user=student)

        if identified:
            # Opening a named profile is a personal-data access, and the trail
            # is what makes that auditable rather than merely permitted.
            log_action(
                action=AuditAction.PII_ACCESS,
                obj=profile,
                actor=request.user,
                note=f"candidate view for vacancy {vacancy.id}",
                severity=AuditSeverity.NOTICE,
            )

        application = (
            Application.objects.filter(student=student, vacancy=vacancy)
            .order_by("-created_at")
            .first()
        )

        # The invite button needs to know what has already been said, so the
        # page can offer "invite", "schedule" or "waiting for a reply" rather
        # than one button that sometimes fails.
        from ..models import InterviewInvite

        last_invite = (
            InterviewInvite.objects.filter(vacancy=vacancy, student=student)
            .order_by("-created_at")
            .first()
        )

        # Each skill carries the proof behind it. A bare number invites the
        # question the employer will ask anyway — "says who?" — and the trail
        # already holds the answer: which source, what it scored, how much that
        # source counts for, and when. Prefetched, so forty skills stay one
        # extra query rather than forty.
        skills = [
            {
                "skill_id": str(link.skill_id),
                "skill": link.skill.name,
                "proficiency": link.proficiency,
                "band": link.band,
                "verified": link.is_verified,
                "source": link.best_source,
                "last_evidence_at": link.last_evidence_at,
                "evidence": [
                    {
                        "source": item.source,
                        "score": item.score,
                        "weight": float(item.weight),
                        "issued_at": item.issued_at,
                        "note": item.note,
                    }
                    for item in link.evidence.all()
                ],
            }
            for link in UserSkill.objects.filter(user=student)
            .select_related("skill")
            .prefetch_related("evidence")
            .order_by("-proficiency")[:40]
        ]

        # Best passed attempt per test: a list showing four tries at the same
        # test reads as a weakness when it is really one result.
        best_by_test: dict = {}
        for attempt in (
            # GRADED is the status a finished attempt actually lands on;
            # SUBMITTED is included because both appear in the codebase and
            # filtering on one alone silently returns nothing.
            TestAttempt.objects.filter(
                user=student,
                status__in=[AttemptStatus.GRADED, AttemptStatus.SUBMITTED],
                passed=True,
            )
            .select_related("test")
            .order_by("test_id", "-percentage")
        ):
            current = best_by_test.get(attempt.test_id)
            if current is None or attempt.percentage > current["percentage"]:
                best_by_test[attempt.test_id] = {
                    "test_id": str(attempt.test_id),
                    "title": attempt.test.title,
                    "percentage": attempt.percentage,
                    "passed": attempt.passed,
                    "submitted_at": attempt.submitted_at,
                }
        tests = sorted(
            best_by_test.values(), key=lambda row: row["percentage"], reverse=True
        )

        experience = [
            {
                "type": entry.type,
                "title": entry.title,
                # The organisation names the person as surely as their surname
                # does in a country this size.
                "organization": entry.organization if identified else None,
                "duration_months": entry.duration_months,
                "is_current": entry.is_current,
                "verified": entry.verification_status == "VERIFIED",
            }
            for entry in Experience.objects.filter(user=student)[:20]
        ]

        certificates = [
            {
                "course": certificate.course.title,
                "issued_at": certificate.issued_at,
                # The serial resolves to a public verification page carrying
                # the holder's name, so it is part of the identity, not of the
                # achievement.
                "serial": certificate.serial if identified else None,
            }
            for certificate in Certificate.objects.filter(user=student)
            .select_related("course")[:20]
        ]

        return Response(
            {
                "user_id": str(student.id),
                "identified": identified,
                "youth_id": getattr(profile, "youth_id", None),
                "name": profile.full_name if (identified and profile) else None,
                "avatar": (
                    profile.avatar.url
                    if identified and profile and profile.avatar
                    else None
                ),
                "bio": profile.bio if (identified and profile) else "",
                "region": (
                    profile.region.name if profile and profile.region_id else None
                ),
                "city": getattr(profile, "city", "") if identified else "",
                "education_status": getattr(profile, "education_status", None),
                "institution": (
                    getattr(profile, "institution", "") if identified else ""
                ),
                "study_year": getattr(profile, "study_year", None),
                "languages": getattr(profile, "languages", []) or [],
                "target_profession": (
                    profile.target_profession.name
                    if profile and profile.target_profession_id
                    else None
                ),
                "open_to_work": getattr(profile, "open_to_work", False),
                "has_applied": application is not None,
                "application": (
                    {
                        "id": str(application.id),
                        "status": application.status,
                        "created_at": application.created_at,
                    }
                    if application
                    else None
                ),
                "invite": (
                    {
                        "id": str(last_invite.id),
                        "status": last_invite.status,
                        "proposed_at": last_invite.proposed_at,
                        "created_at": last_invite.created_at,
                        "response_note": last_invite.response_note,
                    }
                    if last_invite
                    else None
                ),
                "match": {
                    "overall": match.overall_score,
                    "coverage": match.coverage_score,
                    "knowledge": match.knowledge_score,
                    "verification": match.verification_score,
                    "experience": match.experience_score,
                    "education": match.education_score,
                    "location": match.location_score,
                },
                "explanation": match.explanation,
                "required_skills": match.matched_skills,
                "missing_skills": match.missing_skills,
                "skills": skills,
                "tests": tests,
                "experience_entries": experience,
                "certificates": certificates,
            }
        )


@extend_schema(tags=["jobs"])
class ApplicationViewSet(viewsets.ModelViewSet):
    """Students see their own applications; employers see those for their
    vacancies. The queryset is what enforces that, not the UI."""

    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "vacancy"]

    def get_queryset(self):
        user = self.request.user
        base = (
            Application.objects.select_related(
                "vacancy", "vacancy__employer", "student", "student__student_profile"
            )
            .prefetch_related("events__actor", "interviews")
            .order_by("-applied_at")
        )
        if user.is_admin:
            return base
        if user.role == Role.EMPLOYER:
            company = getattr(user, "employer_profile", None)
            return base.filter(vacancy__employer=company)
        return base.filter(student=user)

    def get_serializer_class(self):
        if self.request.user.role in {Role.EMPLOYER, Role.ADMIN}:
            return EmployerApplicationSerializer
        return ApplicationSerializer

    @extend_schema(
        request=CreateApplicationSerializer, responses={201: ApplicationSerializer}
    )
    def create(self, request, *args, **kwargs):
        if request.user.role != Role.STUDENT:
            raise NotAllowed("Only students can apply to vacancies.")

        from apps.billing.enums import Feature as BillingFeature
        from apps.billing.services import consume

        consume(request.user, BillingFeature.JOB_APPLICATION)

        serializer = CreateApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        vacancy = Vacancy.objects.filter(id=data["vacancy"]).first()
        if vacancy is None:
            raise DomainError("Unknown vacancy.", code="unknown_vacancy")

        cv = None
        if data.get("cv"):
            from apps.cv.models import CVDocument

            cv = CVDocument.objects.filter(id=data["cv"], user=request.user).first()

        application = apply_to_vacancy(
            student=request.user,
            vacancy=vacancy,
            cv=cv,
            cover_letter=data.get("cover_letter", ""),
        )
        return Response(
            ApplicationSerializer(application).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(request=dict, responses={200: EmployerApplicationSerializer})
    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, pk=None):
        application = self.get_object()
        user = request.user

        if user.role == Role.STUDENT:
            # A student may only withdraw; everything else is the employer's call.
            if request.data.get("status") != "WITHDRAWN":
                raise NotAllowed("Students can only withdraw an application.")
            application = withdraw_application(application, actor=user)
        else:
            application = change_application_status(
                application,
                to_status=request.data.get("status", ""),
                actor=user,
                note=request.data.get("note", ""),
            )
        return Response(self.get_serializer(application).data)

    @extend_schema(responses={200: dict})
    @action(detail=True, methods=["get"], url_path="explain")
    def explain(self, request, pk=None):
        """"Why this candidate?" for the employer (prompt §19)."""
        application = self.get_object()
        if request.user.role == Role.STUDENT:
            raise NotAllowed("Not available for students.")

        from apps.ai.services import get_ai_service

        explanation = get_ai_service().explain_candidate(
            application.student, application.vacancy
        )
        return Response(
            {
                "summary_code": explanation.summary_code,
                "reasons": explanation.reasons,
                "data": explanation.data,
            }
        )


@extend_schema(tags=["jobs"])
class InterviewViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = InterviewSerializer

    def get_queryset(self):
        user = self.request.user
        base = Interview.objects.select_related("application__vacancy__employer")
        if user.is_admin:
            return base
        if user.role == Role.EMPLOYER:
            return base.filter(
                application__vacancy__employer=getattr(user, "employer_profile", None)
            )
        return base.filter(application__student=user)

    def perform_create(self, serializer):
        if self.request.user.role not in {Role.EMPLOYER, Role.ADMIN}:
            raise NotAllowed("Only employers can schedule interviews.")

        # The queryset scopes *reads*. Creation takes an application id from
        # the request body, and without this check any employer could schedule
        # an interview on a rival's application — the candidate would then be
        # notified about a meeting the company they applied to never arranged.
        application = serializer.validated_data.get("application")
        if application is not None and not self.request.user.is_admin:
            company = getattr(self.request.user, "employer_profile", None)
            if company is None or application.vacancy.employer_id != company.id:
                raise NotAllowed("This application belongs to another company.")

        interview = serializer.save()

        from apps.analytics.services import track
        from apps.notifications.services import notify

        track(
            interview.application.student,
            "interview_scheduled",
            {"application_id": str(interview.application_id)},
        )
        notify(
            user=interview.application.student,
            type="INTERVIEW_SCHEDULED",
            title_key="notifications.interview.scheduled.title",
            body_key="notifications.interview.scheduled.body",
            payload={
                "vacancy": interview.application.vacancy.title,
                "scheduled_at": interview.scheduled_at.isoformat(),
            },
            ref_type="Interview",
            ref_id=interview.id,
            action_url=f"/student/applications/{interview.application_id}",
        )


@extend_schema(tags=["jobs"])
class SavedVacancyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsStudent]
    serializer_class = SavedVacancySerializer

    def get_queryset(self):
        return SavedVacancy.objects.filter(user=self.request.user).select_related(
            "vacancy", "vacancy__employer"
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["jobs"])
class PlacementViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PlacementSerializer

    def get_queryset(self):
        user = self.request.user
        base = Placement.objects.select_related(
            "student__student_profile", "employer", "vacancy"
        )
        if user.is_admin:
            return base
        if user.role == Role.EMPLOYER:
            return base.filter(employer=getattr(user, "employer_profile", None))
        return base.filter(student=user)

    def perform_update(self, serializer):
        """Income is only recorded with the student's explicit consent."""
        from apps.accounts.models import ConsentType
        from apps.accounts.services import has_consent

        placement = self.get_object()
        if serializer.validated_data.get("income_reported") is not None:
            if not has_consent(placement.student, ConsentType.INCOME_TRACKING):
                raise NotAllowed(
                    "The student has not consented to income tracking.",
                    code="consent_required",
                )
        serializer.save()


@extend_schema(tags=["jobs"])
class EmployerDashboardView(APIView):
    """Headline numbers for the employer portal (prompt §14)."""

    permission_classes = [IsAuthenticated, IsEmployer]

    def get(self, request):
        from django.db.models import Avg, Count

        from apps.assessment.models import Test, TestAttempt
        from apps.learning.models import Course, Enrollment, EnrollmentStatus
        from apps.matching.models import MatchResult

        company = getattr(request.user, "employer_profile", None)
        if company is None:
            raise DomainError("No company profile.", code="no_profile")

        vacancies = Vacancy.objects.filter(employer=company)
        applications = Application.objects.filter(vacancy__employer=company)
        courses = Course.objects.filter(employer=company)
        tests = Test.objects.filter(employer=company)

        enrollments = Enrollment.objects.filter(course__employer=company)
        attempts = TestAttempt.objects.filter(test__employer=company, status="GRADED")

        return Response(
            {
                "company": {
                    "id": str(company.id),
                    "name": company.display_name,
                    "verification_status": company.verification_status,
                },
                "vacancies": {
                    "total": vacancies.count(),
                    "published": vacancies.filter(
                        status=ModerationStatus.PUBLISHED
                    ).count(),
                    "pending": vacancies.filter(
                        status=ModerationStatus.PENDING_REVIEW
                    ).count(),
                    "draft": vacancies.filter(status=ModerationStatus.DRAFT).count(),
                },
                "applications": {
                    "total": applications.count(),
                    "new": applications.filter(status="APPLIED").count(),
                    "shortlisted": applications.filter(status="SHORTLISTED").count(),
                    "interview": applications.filter(status="INTERVIEW").count(),
                    "hired": applications.filter(status="ACCEPTED").count(),
                },
                "learning": {
                    "courses": courses.count(),
                    "students": enrollments.values("user").distinct().count(),
                    "completions": enrollments.filter(
                        status=EnrollmentStatus.COMPLETED
                    ).count(),
                    "avg_completion_rate": round(
                        courses.aggregate(v=Avg("completion_count"))["v"] or 0
                    ),
                },
                "assessment": {
                    "tests": tests.count(),
                    "attempts": attempts.count(),
                    "avg_score": round(
                        attempts.aggregate(v=Avg("percentage"))["v"] or 0
                    ),
                },
                "talent": {
                    "strong_matches": MatchResult.objects.filter(
                        vacancy__employer=company, overall_score__gte=70
                    ).count(),
                    "top_vacancies": list(
                        vacancies.filter(status=ModerationStatus.PUBLISHED)
                        .annotate(applicant_count=Count("applications"))
                        .order_by("-applicant_count")
                        .values("id", "title", "applicant_count")[:5]
                    ),
                },
            }
        )


@extend_schema(tags=["jobs"])
class InterviewInviteViewSet(viewsets.ModelViewSet):
    """Invitations to talk, from both sides.

    An employer creates and withdraws them; a student answers them. Both use
    the same collection because it is one object with two audiences, and the
    queryset is what keeps each side to its own rows.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = InterviewInviteSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        from ..models import InterviewInvite

        user = self.request.user
        base = InterviewInvite.objects.select_related(
            "vacancy", "vacancy__employer", "student", "student__student_profile"
        )
        if user.is_admin:
            return base
        if user.role == Role.EMPLOYER:
            company = getattr(user, "employer_profile", None)
            if company is None:
                return base.none()
            return base.filter(vacancy__employer=company)
        return base.filter(student=user)

    def create(self, request, *args, **kwargs):
        """Employer: invite this candidate on this vacancy."""
        from apps.profiles.services import can_view_student_profile

        from ..models import InterviewInvite
        from ..services import invite_to_interview

        if request.user.role not in {Role.EMPLOYER, Role.ADMIN}:
            raise NotAllowed("Only employers can invite candidates.")

        vacancy = Vacancy.objects.filter(id=request.data.get("vacancy")).first()
        if vacancy is None:
            raise DomainError("That vacancy was not found.", code="unknown_vacancy")

        company = getattr(request.user, "employer_profile", None)
        if not request.user.is_admin and (
            company is None or vacancy.employer_id != company.id
        ):
            raise NotAllowed("This vacancy belongs to another company.")

        from apps.accounts.models import User
        from apps.matching.models import MatchResult

        student = User.objects.filter(
            id=request.data.get("student"), role=Role.STUDENT
        ).first()
        if student is None:
            raise DomainError("That candidate was not found.", code="not_found")

        # Same reachability rule as the candidate card: the invitation follows
        # a match, so the endpoint cannot be used to message arbitrary users.
        if not MatchResult.objects.filter(vacancy=vacancy, student=student).exists():
            raise NotAllowed(
                "This person is not a candidate for this vacancy.", code="not_found"
            )

        proposed_at = request.data.get("proposed_at") or None
        if proposed_at:
            parsed = parse_datetime(proposed_at)
            if parsed is None:
                raise DomainError("That date could not be read.", code="invalid_date")
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            if parsed <= timezone.now():
                raise DomainError(
                    "An interview cannot be scheduled in the past.",
                    code="interview_in_the_past",
                )
            proposed_at = parsed

        result, interview = invite_to_interview(
            vacancy=vacancy,
            student=student,
            actor=request.user,
            message=str(request.data.get("message", ""))[:2000],
            proposed_at=proposed_at,
            duration_minutes=int(request.data.get("duration_minutes") or 45),
            mode=request.data.get("mode") or "ONLINE",
            location=str(request.data.get("location", ""))[:255],
            meeting_link=str(request.data.get("meeting_link", ""))[:200],
        )

        if isinstance(result, InterviewInvite):
            return Response(
                {
                    "kind": "invite",
                    "invite": InterviewInviteSerializer(result).data,
                },
                status=status.HTTP_201_CREATED,
            )

        # They had already applied, so this went straight to a booking.
        return Response(
            {
                "kind": "scheduled",
                "application": str(result.id),
                "status": result.status,
                "interview": InterviewSerializer(interview).data if interview else None,
                "identified": can_view_student_profile(
                    viewer=request.user, student_user=student
                ),
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(responses={200: dict})
    @action(detail=True, methods=["post"])
    def respond(self, request, pk=None):
        """Student: accept or decline."""
        from ..services import respond_to_invite

        invite = self.get_object()
        if invite.student_id != request.user.id:
            raise NotAllowed("This invitation is addressed to somebody else.")

        accept = request.data.get("accept")
        if accept is None:
            raise DomainError("Say whether the invitation is accepted.", code="validation_error")

        invite, application, interview = respond_to_invite(
            invite,
            accept=bool(accept),
            note=str(request.data.get("note", ""))[:1000],
        )
        return Response(
            {
                "invite": InterviewInviteSerializer(invite).data,
                "application": str(application.id) if application else None,
                "interview": InterviewSerializer(interview).data if interview else None,
            }
        )

    def destroy(self, request, *args, **kwargs):
        """Employer: withdraw an invitation nobody has answered."""
        from ..services import cancel_invite

        invite = self.get_object()
        if request.user.role not in {Role.EMPLOYER, Role.ADMIN}:
            raise NotAllowed("Only the employer can withdraw an invitation.")
        cancel_invite(invite, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
