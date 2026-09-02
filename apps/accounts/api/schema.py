"""OpenAPI extensions.

Without this, drf-spectacular cannot describe our custom authentication class
and Swagger UI renders no "Authorize" button.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class CookieJWTScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.authentication.CookieJWTAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "Short-lived access token from POST /api/v1/auth/login/. "
                "The refresh token is delivered as an httpOnly cookie and is "
                "never exposed to JavaScript."
            ),
        }
