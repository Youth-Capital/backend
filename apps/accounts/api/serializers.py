"""Authentication serializers."""

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.common.enums import Language, Role

from ..models import Consent, ConsentType, User
from ..services import SELF_SERVICE_ROLES


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=10, max_length=128)
    role = serializers.ChoiceField(choices=[(r, r) for r in sorted(SELF_SERVICE_ROLES)])
    preferred_language = serializers.ChoiceField(
        choices=Language.choices, default=Language.UZ
    )
    phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    consents = serializers.ListField(
        child=serializers.ChoiceField(choices=ConsentType.choices), allow_empty=False
    )

    # Student fields
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    birth_date = serializers.DateField(required=False, allow_null=True)
    region_id = serializers.UUIDField(required=False, allow_null=True)

    # Employer fields
    company_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    legal_name = serializers.CharField(required=False, allow_blank=True, max_length=255)

    # Mentor fields
    headline = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate_email(self, value: str) -> str:
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def validate(self, attrs):
        role = attrs["role"]
        if role == Role.STUDENT and not attrs.get("birth_date"):
            raise serializers.ValidationError(
                {"birth_date": "Date of birth is required for student accounts."}
            )
        if role == Role.EMPLOYER and not (
            attrs.get("company_name") or attrs.get("legal_name")
        ):
            raise serializers.ValidationError(
                {"company_name": "Company name is required for employer accounts."}
            )
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class UserSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    profile = serializers.SerializerMethodField()
    requires_guardian_approval = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "phone",
            "role",
            "preferred_language",
            "email_verified",
            "phone_verified",
            "display_name",
            "date_joined",
            "profile",
            "requires_guardian_approval",
        ]
        read_only_fields = [
            "id",
            "role",
            "email_verified",
            "phone_verified",
            "date_joined",
        ]

    def get_profile(self, user) -> dict | None:
        """A compact profile stub so the SPA can render the shell immediately."""
        if user.role == Role.STUDENT:
            profile = getattr(user, "student_profile", None)
            if profile is None:
                return None
            return {
                "id": str(profile.id),
                "youth_id": profile.youth_id,
                "first_name": profile.first_name,
                "last_name": profile.last_name,
                "avatar": profile.avatar.url if profile.avatar else None,
                "profile_completion": profile.profile_completion,
                "onboarding_completed": profile.onboarding_completed_at is not None,
                "target_profession_id": str(profile.target_profession_id)
                if profile.target_profession_id
                else None,
            }
        if user.role == Role.EMPLOYER:
            company = getattr(user, "employer_profile", None)
            if company is None:
                return None
            return {
                "id": str(company.id),
                "name": company.display_name,
                "slug": company.slug,
                "logo": company.logo.url if company.logo else None,
                "verification_status": company.verification_status,
            }
        if user.role == Role.MENTOR:
            mentor = getattr(user, "mentor_profile", None)
            if mentor is None:
                return None
            return {
                "id": str(mentor.id),
                "name": mentor.full_name,
                "headline": mentor.headline,
                "avatar": mentor.avatar.url if mentor.avatar else None,
                "verification_status": mentor.verification_status,
            }
        return None

    def get_requires_guardian_approval(self, user) -> bool:
        from ..services import requires_guardian_approval

        return requires_guardian_approval(user)


class ConsentSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Consent
        fields = ["id", "type", "version", "granted", "granted_at", "revoked_at", "is_active"]
        read_only_fields = fields


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=10, max_length=128)

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=10, max_length=128)

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value


class TokenResponseSerializer(serializers.Serializer):
    """Only the access token is returned in the body.

    The refresh token is set as an httpOnly cookie and is intentionally absent
    here — if JavaScript can read it, XSS can steal it.
    """

    access = serializers.CharField()
    user = UserSerializer()
