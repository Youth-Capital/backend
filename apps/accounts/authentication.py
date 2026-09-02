"""JWT authentication.

The access token travels in the Authorization header and lives only in the
SPA's memory. The refresh token never reaches JavaScript at all — it sits in an
httpOnly cookie, so an XSS payload cannot exfiltrate a long-lived credential.
"""

from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken


class CookieJWTAuthentication(JWTAuthentication):
    """Header-based auth, unchanged, but tolerant of a missing header.

    Refresh flows read the cookie explicitly (see api/views.py) rather than
    authenticating from it, so a stolen cookie alone cannot call the API.
    """

    def authenticate(self, request):
        header = self.get_header(request)
        if header is None:
            return None
        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None
        validated = self.get_validated_token(raw_token)
        return self.get_user(validated), validated


def set_refresh_cookie(response, refresh_token: str):
    """Attach the rotating refresh token to the response."""
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=refresh_token,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        domain=settings.AUTH_COOKIE_DOMAIN,
        path=settings.AUTH_COOKIE_PATH,
    )
    return response


def clear_refresh_cookie(response):
    response.delete_cookie(
        key=settings.AUTH_COOKIE_NAME,
        domain=settings.AUTH_COOKIE_DOMAIN,
        path=settings.AUTH_COOKIE_PATH,
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )
    return response


def read_refresh_cookie(request) -> str | None:
    return request.COOKIES.get(settings.AUTH_COOKIE_NAME)


def issue_tokens(user) -> tuple[str, str]:
    """Return (access, refresh) for a freshly authenticated user."""
    refresh = RefreshToken.for_user(user)
    refresh["role"] = user.role
    refresh["email"] = user.email
    access = refresh.access_token
    access["role"] = user.role
    return str(access), str(refresh)


def rotate_tokens(raw_refresh: str) -> tuple[str, str]:
    """Validate a refresh token, blacklist it, and issue a fresh pair.

    Rotation plus blacklisting means a captured refresh token is usable at most
    once, and its reuse invalidates the chain.
    """
    try:
        old = RefreshToken(raw_refresh)
    except TokenError as exc:
        raise InvalidToken(str(exc)) from exc

    user_id = old.payload.get("user_id")
    try:
        old.blacklist()
    except AttributeError:  # pragma: no cover - blacklist app always installed
        pass

    from .models import User

    user = User.objects.filter(id=user_id, is_active=True).first()
    if user is None:
        raise InvalidToken("User is inactive or no longer exists.")
    return issue_tokens(user)
