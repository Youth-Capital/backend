"""Edu-Job serializers."""

from rest_framework import serializers

from apps.common.serializers import TranslatedField

from ..models import (
    Application,
    ApplicationEvent,
    Interview,
    InterviewInvite,
    Placement,
    SavedVacancy,
    Vacancy,
    VacancySkill,
)


class VacancySkillSerializer(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")
    category = serializers.CharField(source="skill.category.slug", read_only=True)

    class Meta:
        model = VacancySkill
        fields = [
            "id",
            "skill",
            "skill_name",
            "category",
            "requirement",
            "min_knowledge_score",
            "weight",
            "order",
        ]


class VacancyListSerializer(serializers.ModelSerializer):
    company = serializers.SerializerMethodField()
    region_name = TranslatedField("name", source="region")
    profession_name = TranslatedField("name", source="profession")
    required_skills = serializers.SerializerMethodField()
    is_open = serializers.BooleanField(read_only=True)
    my_match = serializers.SerializerMethodField()
    my_application = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()

    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "language",
            "employment_type",
            "work_mode",
            "region",
            "region_name",
            "city",
            "profession",
            "profession_name",
            "min_experience_months",
            "education_required",
            "salary_min",
            "salary_max",
            "currency",
            "is_salary_public",
            "positions_count",
            "deadline",
            "status",
            "published_at",
            "views_count",
            "company",
            "required_skills",
            "is_open",
            "my_match",
            "my_application",
            "is_saved",
        ]

    def get_company(self, vacancy) -> dict:
        employer = vacancy.employer
        return {
            "id": str(employer.id),
            "name": employer.display_name,
            "slug": employer.slug,
            "logo": employer.logo.url if employer.logo else None,
            "is_verified": employer.is_verified,
        }

    def get_required_skills(self, vacancy) -> list[dict]:
        return [
            {
                "id": str(link.skill_id),
                "name": link.skill.name,
                "requirement": link.requirement,
                "min_score": link.min_knowledge_score,
            }
            for link in vacancy.skill_links.all()
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Salary stays hidden unless the employer chose to publish it.
        if not instance.is_salary_public:
            data["salary_min"] = None
            data["salary_max"] = None
        return data

    def get_my_match(self, vacancy) -> dict | None:
        cache = self.context.get("matches_by_vacancy")
        if cache is None:
            return None
        match = cache.get(vacancy.id)
        if match is None:
            return None
        return {
            "score": match.overall_score,
            "coverage": match.coverage_score,
            "knowledge": match.knowledge_score,
            "missing_skills": [s["skill"] for s in match.missing_skills[:4]],
            # Why this is in front of them at all, which is a different
            # question from how well they fit it. A card showing 88% and
            # nothing else cannot tell somebody whether it is 88% of a job
            # they want.
            "relevance": match.relevance_score,
            "relevance_known": match.relevance_known,
            "relevance_reason": (
                match.relevance_reasons[0]["code"]
                if match.relevance_reasons
                else ""
            ),
        }

    def get_my_application(self, vacancy) -> dict | None:
        cache = self.context.get("applications_by_vacancy")
        if cache is None:
            return None
        application = cache.get(vacancy.id)
        if application is None:
            return None
        return {"id": str(application.id), "status": application.status}

    def get_is_saved(self, vacancy) -> bool:
        saved = self.context.get("saved_vacancy_ids")
        return bool(saved and vacancy.id in saved)


class VacancyDetailSerializer(VacancyListSerializer):
    skills = VacancySkillSerializer(source="skill_links", many=True, read_only=True)
    screening_tests = serializers.SerializerMethodField()
    match_explanation = serializers.SerializerMethodField()

    class Meta(VacancyListSerializer.Meta):
        fields = [
            *VacancyListSerializer.Meta.fields,
            "description",
            "responsibilities",
            "conditions",
            "skills",
            "screening_tests",
            "match_explanation",
            "moderation_note",
        ]

    def get_screening_tests(self, vacancy) -> list[dict]:
        return [
            {
                "id": str(link.test_id),
                "title": link.test.title,
                "is_mandatory": link.is_mandatory,
            }
            for link in vacancy.screening_tests.select_related("test")
        ]

    def get_match_explanation(self, vacancy) -> list | None:
        cache = self.context.get("matches_by_vacancy")
        match = cache.get(vacancy.id) if cache else None
        return match.explanation if match else None


class VacancyWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "description",
            "responsibilities",
            "conditions",
            "language",
            "employment_type",
            "work_mode",
            "region",
            "city",
            "profession",
            "min_experience_months",
            "education_required",
            "salary_min",
            "salary_max",
            "currency",
            "is_salary_public",
            "positions_count",
            "deadline",
        ]

    def validate(self, attrs):
        salary_min = attrs.get("salary_min")
        salary_max = attrs.get("salary_max")
        if salary_min and salary_max and salary_max < salary_min:
            raise serializers.ValidationError(
                {"salary_max": "Maximum salary cannot be lower than the minimum."}
            )
        return attrs


class ApplicationEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = ApplicationEvent
        fields = ["id", "from_status", "to_status", "actor_name", "note", "created_at"]
        read_only_fields = fields

    def get_actor_name(self, event) -> str:
        return event.actor.display_name if event.actor else "system"


class InterviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Interview
        fields = [
            "id",
            "application",
            "scheduled_at",
            "duration_minutes",
            "mode",
            "location",
            "meeting_link",
            "status",
            "feedback",
        ]
        read_only_fields = ["id"]


class InterviewInviteSerializer(serializers.ModelSerializer):
    """Both sides read this, so it carries each side's missing half.

    The employer knows the vacancy and needs the candidate; the student knows
    themselves and needs the company. Neither gets the other's identity beyond
    what they are already entitled to: `candidate_name` is filled only when the
    same rule that governs the candidate card says it may be.
    """

    vacancy_title = serializers.CharField(source="vacancy.title", read_only=True)
    company = serializers.CharField(
        source="vacancy.employer.display_name", read_only=True
    )
    candidate_name = serializers.SerializerMethodField()
    youth_id = serializers.SerializerMethodField()

    class Meta:
        model = InterviewInvite
        fields = [
            "id",
            "vacancy",
            "vacancy_title",
            "company",
            "student",
            "candidate_name",
            "youth_id",
            "message",
            "proposed_at",
            "duration_minutes",
            "mode",
            "location",
            "meeting_link",
            "status",
            "responded_at",
            "response_note",
            "match_score_at_invite",
            "created_at",
        ]
        read_only_fields = fields

    def get_youth_id(self, invite) -> str | None:
        profile = getattr(invite.student, "student_profile", None)
        return getattr(profile, "youth_id", None)

    def get_candidate_name(self, invite) -> str | None:
        request = self.context.get("request")
        if request is None:
            return None
        from apps.profiles.services import can_view_student_profile

        if not can_view_student_profile(
            viewer=request.user, student_user=invite.student
        ):
            return None
        profile = getattr(invite.student, "student_profile", None)
        return getattr(profile, "full_name", None)


class ApplicationSerializer(serializers.ModelSerializer):
    vacancy_detail = serializers.SerializerMethodField()
    events = ApplicationEventSerializer(many=True, read_only=True)
    interviews = InterviewSerializer(many=True, read_only=True)

    class Meta:
        model = Application
        fields = [
            "id",
            "vacancy",
            "vacancy_detail",
            "cv",
            "cover_letter",
            "status",
            "match_score_at_apply",
            "applied_at",
            "status_changed_at",
            "employer_note",
            "events",
            "interviews",
        ]
        read_only_fields = [
            "id",
            "status",
            "match_score_at_apply",
            "applied_at",
            "status_changed_at",
            "employer_note",
        ]

    def get_vacancy_detail(self, application) -> dict:
        vacancy = application.vacancy
        return {
            "id": str(vacancy.id),
            "title": vacancy.title,
            "company": vacancy.employer.display_name,
            "employment_type": vacancy.employment_type,
            "work_mode": vacancy.work_mode,
            "city": vacancy.city,
        }


class EmployerApplicationSerializer(ApplicationSerializer):
    """Employer view — adds the candidate, resolved through the same
    consent rules as talent search."""

    candidate = serializers.SerializerMethodField()
    cv_rating = serializers.SerializerMethodField()

    class Meta(ApplicationSerializer.Meta):
        fields = [*ApplicationSerializer.Meta.fields, "candidate", "cv_rating"]

    def get_cv_rating(self, application) -> int | None:
        """The stored rating of the CV that was actually sent.

        Falls back to the candidate's primary CV when the application carried
        none, which is what the employer is looking at in that case anyway.
        The stored column is used rather than a recompute, because this
        serialiser renders a whole list.
        """
        cache = self.context.get("cv_rating_by_user")
        if application.cv_id and application.cv is not None:
            return application.cv.quality_score
        if cache is not None:
            return cache.get(application.student_id)
        return None

    def get_candidate(self, application) -> dict:
        profile = getattr(application.student, "student_profile", None)
        if profile is None:
            return {"identified": False}
        # Applying is itself consent to be seen by that employer.
        return {
            "identified": True,
            "user_id": str(application.student_id),
            "youth_id": profile.youth_id,
            "name": profile.full_name,
            "avatar": profile.avatar.url if profile.avatar else None,
            "education_status": profile.education_status,
            "city": profile.city,
        }


class CreateApplicationSerializer(serializers.Serializer):
    vacancy = serializers.UUIDField()
    cv = serializers.UUIDField(required=False, allow_null=True)
    cover_letter = serializers.CharField(
        required=False, allow_blank=True, max_length=4000
    )


class SavedVacancySerializer(serializers.ModelSerializer):
    vacancy_detail = VacancyListSerializer(source="vacancy", read_only=True)

    class Meta:
        model = SavedVacancy
        fields = ["id", "vacancy", "vacancy_detail", "created_at"]
        read_only_fields = ["id", "created_at"]


class PlacementSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    employer_name = serializers.CharField(source="employer.display_name", read_only=True)

    class Meta:
        model = Placement
        fields = [
            "id",
            "student",
            "student_name",
            "employer",
            "employer_name",
            "vacancy",
            "position",
            "start_date",
            "end_date",
            "status",
            "retention_30",
            "retention_90",
            "retention_180",
            "income_reported",
            "income_currency",
        ]
        read_only_fields = ["id", "retention_30", "retention_90", "retention_180"]

    def get_student_name(self, placement) -> str:
        profile = getattr(placement.student, "student_profile", None)
        return profile.full_name if profile else ""


class CandidateSerializer(serializers.Serializer):
    """A ranked candidate row (prompt §18)."""

    user_id = serializers.CharField()
    youth_id = serializers.CharField(allow_null=True)
    name = serializers.CharField(allow_null=True)
    identified = serializers.BooleanField()
    match = serializers.DictField()
    skills = serializers.ListField()
    missing_skills = serializers.ListField()
    explanation = serializers.ListField()
