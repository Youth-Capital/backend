"""Authentication endpoints."""

from django.contrib.auth import authenticate
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken

from apps.audit.models import AuditAction, AuditSeverity
from apps.audit.services import log_action
from apps.common.context import get_client_ip
from apps.common.exceptions import DomainError

from ..authentication import (
    clear_refresh_cookie,
    issue_tokens,
    read_refresh_cookie,
    rotate_tokens,
    set_refresh_cookie,
)
from ..models import Consent, User
from ..services import (
    change_password,
    confirm_email,
    create_email_verification,
    create_password_reset,
    grant_consent,
    register_user,
    reset_password,
    revoke_consent,
)
from .serializers import (
    ChangePasswordSerializer,
    ConsentSerializer,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)


def _auth_response(user, *, status_code=status.HTTP_200_OK):
    access, refresh = issue_tokens(user)
    response = Response(
        {"access": access, "user": UserSerializer(user).data}, status=status_code
    )
    return set_refresh_cookie(response, refresh)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    @extend_schema(request=RegisterSerializer, responses={201: dict}, tags=["auth"])
    @transaction.atomic
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = register_user(
            email=data["email"],
            password=data["password"],
            role=data["role"],
            preferred_language=data.get("preferred_language", "uz"),
            birth_date=data.get("birth_date"),
            phone=data.get("phone") or None,
            consents=data["consents"],
            ip=get_client_ip(request),
            user_agent=request.headers.get("User-Agent", ""),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            region_id=data.get("region_id"),
            company_name=data.get("company_name", ""),
            legal_name=data.get("legal_name", ""),
            headline=data.get("headline", ""),
        )

        token = create_email_verification(user)
        log_action(action=AuditAction.CREATE, obj=user, actor=user, note="registration")

        from apps.analytics.services import track

        track(user, "signed_up", {"role": user.role})

        response = _auth_response(user, status_code=status.HTTP_201_CREATED)
        if request.headers.get("X-Debug-Return-Token") and __debug__:
            # Dev convenience only; the console email backend already logs it.
            response.data["email_verification_token"] = token
        return response


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(request=LoginSerializer, responses={200: dict}, tags=["auth"])
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # `request` is passed so django-axes can see the attempt.
        user = authenticate(
            request,
            username=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            log_action(
                action=AuditAction.LOGIN_FAILED,
                object_type="User",
                object_repr=serializer.validated_data["email"][:200],
                severity=AuditSeverity.WARNING,
            )
            # One generic message: distinguishing "no such user" from "wrong
            # password" hands an attacker a user-enumeration oracle.
            raise DomainError(
                "Invalid email or password.",
                code="invalid_credentials",
            ) from None

        if not user.is_active:
            raise DomainError("This account is disabled.", code="account_disabled")

        user.last_login_ip = get_client_ip(request)
        user.save(update_fields=["last_login_ip"])

        log_action(action=AuditAction.LOGIN, obj=user, actor=user)
        return _auth_response(user)


class RefreshView(APIView):
    """Rotate the refresh cookie and mint a new access token."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    #: Its own budget, not the sign-in one — see DEFAULT_THROTTLE_RATES.
    throttle_scope = "refresh"

    @extend_schema(request=None, responses={200: dict}, tags=["auth"])
    def post(self, request):
        raw = read_refresh_cookie(request)
        if not raw:
            raise DomainError(
                "No refresh token.", code="no_refresh_token", details={}
            ) from None

        try:
            access, refresh = rotate_tokens(raw)
        except InvalidToken:
            response = Response(
                {
                    "error": {
                        "code": "invalid_refresh_token",
                        "message": "Session expired. Please sign in again.",
                        "details": {},
                    }
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
            return clear_refresh_cookie(response)

        from rest_framework_simplejwt.tokens import AccessToken

        user_id = AccessToken(access).payload.get("user_id")
        user = User.objects.filter(id=user_id).first()

        response = Response(
            {"access": access, "user": UserSerializer(user).data if user else None}
        )
        return set_refresh_cookie(response, refresh)


class LogoutView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(request=None, responses={204: None}, tags=["auth"])
    def post(self, request):
        raw = read_refresh_cookie(request)
        if raw:
            try:
                from rest_framework_simplejwt.tokens import RefreshToken

                RefreshToken(raw).blacklist()
            except Exception:
                # An already-invalid token is not an error worth reporting: the
                # user asked to be logged out and now they are.
                pass
        response = Response(status=status.HTTP_204_NO_CONTENT)
        return clear_refresh_cookie(response)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserSerializer}, tags=["auth"])
    def get(self, request):
        from apps.audit.middleware import set_audit_actor
        from apps.profiles.services import touch_activity

        set_audit_actor(request.user)
        touch_activity(request.user)
        return Response(UserSerializer(request.user).data)

    @extend_schema(request=UserSerializer, responses={200: UserSerializer}, tags=["auth"])
    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ConsentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ConsentSerializer(many=True)}, tags=["auth"])
    def get(self, request):
        consents = Consent.objects.filter(user=request.user).order_by("type")
        return Response(ConsentSerializer(consents, many=True).data)

    @extend_schema(request=dict, responses={200: ConsentSerializer}, tags=["auth"])
    def post(self, request):
        consent_type = request.data.get("type")
        granted = bool(request.data.get("granted", True))

        if granted:
            consent = grant_consent(
                request.user,
                consent_type,
                ip=get_client_ip(request),
                user_agent=request.headers.get("User-Agent", ""),
            )
            log_action(
                action=AuditAction.CONSENT_GRANT,
                obj=consent,
                actor=request.user,
                note=consent_type,
            )
            return Response(ConsentSerializer(consent).data)

        revoke_consent(request.user, consent_type)
        log_action(
            action=AuditAction.CONSENT_REVOKE,
            object_type="Consent",
            object_id=str(request.user.id),
            actor=request.user,
            note=consent_type,
            severity=AuditSeverity.NOTICE,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(request=ChangePasswordSerializer, responses={204: None}, tags=["auth"])
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_password(
            request.user,
            serializer.validated_data["current_password"],
            serializer.validated_data["new_password"],
        )
        log_action(
            action=AuditAction.PASSWORD_CHANGE,
            obj=request.user,
            actor=request.user,
            severity=AuditSeverity.NOTICE,
        )
        response = Response(status=status.HTTP_204_NO_CONTENT)
        return clear_refresh_cookie(response)


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        request=PasswordResetRequestSerializer, responses={202: dict}, tags=["auth"]
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"]
        ).first()
        if user is not None:
            token = create_password_reset(user, ip=get_client_ip(request))
            _send_reset_email(user, token)

        # Always 202, whether or not the account exists — anything else tells
        # an attacker which emails are registered.
        return Response(
            {"detail": "If the account exists, a reset link has been sent."},
            status=status.HTTP_202_ACCEPTED,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        request=PasswordResetConfirmSerializer, responses={204: None}, tags=["auth"]
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = reset_password(
            serializer.validated_data["token"], serializer.validated_data["new_password"]
        )
        log_action(
            action=AuditAction.PASSWORD_CHANGE,
            obj=user,
            actor=user,
            note="password reset",
            severity=AuditSeverity.NOTICE,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(request=dict, responses={200: dict}, tags=["auth"])
    def post(self, request):
        token = request.data.get("token", "")
        user = confirm_email(token)
        return Response({"email_verified": True, "email": user.email})


def _send_reset_email(user, token: str) -> None:
    from django.conf import settings
    from django.core.mail import send_mail

    link = f"{settings.FRONTEND_URL}/auth/reset-password?token={token}"
    send_mail(
        subject=f"{settings.PLATFORM_NAME} — password reset",
        message=f"Use this link to reset your password: {link}\nIt expires in 1 hour.",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@yoshlarkapitali.uz"),
        recipient_list=[user.email],
        fail_silently=True,
    )
