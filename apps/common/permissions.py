"""Role and ownership permissions.

These are the *server-side* half of RBAC. The React `RequireRole` guard is UX
only — a hidden button is not access control (prompt §29, §24).
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS

from .enums import Role


class _RolePermission(BasePermission):
    role: str = ""

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.role == self.role)


class IsStudent(_RolePermission):
    role = Role.STUDENT
    message = "Only students can perform this action."


class IsEmployer(_RolePermission):
    role = Role.EMPLOYER
    message = "Only employers can perform this action."


class IsMentor(_RolePermission):
    role = Role.MENTOR
    message = "Only mentors can perform this action."


class IsAdmin(BasePermission):
    message = "Administrator access required."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.role == Role.ADMIN or user.is_superuser)
        )


class IsEmployerOrAdmin(BasePermission):
    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.role in {Role.EMPLOYER, Role.ADMIN} or user.is_superuser)
        )


class IsOwner(BasePermission):
    """Object belongs to the requesting user.

    Looks for `user`, `owner` or `student` — whichever the model uses.
    """

    message = "This object does not belong to you."
    owner_fields = ("user_id", "owner_id", "student_id")

    def has_object_permission(self, request, view, obj) -> bool:
        user_id = request.user.id
        for field in self.owner_fields:
            if hasattr(obj, field):
                return getattr(obj, field) == user_id
        return getattr(obj, "id", None) == user_id


class IsOwnerOrAdmin(IsOwner):
    def has_object_permission(self, request, view, obj) -> bool:
        user = request.user
        if user.role == Role.ADMIN or user.is_superuser:
            return True
        return super().has_object_permission(request, view, obj)


class ReadOnly(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.method in SAFE_METHODS


class IsAuthenticatedReadOnlyOrAdmin(BasePermission):
    """Catalogue endpoints: anyone signed in may read, only admins may write."""

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return user.role == Role.ADMIN or user.is_superuser
