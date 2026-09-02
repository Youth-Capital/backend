"""Authentication backends."""

from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

from .models import User


class EmailOrPhoneBackend(ModelBackend):
    """Sign in with either email or phone number.

    On a miss we still run the password hasher against a dummy hash so the
    response time does not reveal whether the account exists.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get("email") or kwargs.get("phone")
        if not identifier or not password:
            return None

        identifier = identifier.strip()
        try:
            user = User.objects.get(
                Q(email__iexact=identifier) | Q(phone=identifier)
            )
        except User.DoesNotExist:
            User().set_password(password)
            return None
        except User.MultipleObjectsReturned:  # pragma: no cover - unique fields
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
