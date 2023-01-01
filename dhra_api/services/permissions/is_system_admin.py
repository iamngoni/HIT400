from rest_framework.permissions import BasePermission

from users.models import UserRoles


class IsSystemAdmin(BasePermission):
    message = "You must be a system admin to access this resource."

    def has_permission(self, request, view):
        return request.user.role == UserRoles.SYSTEM_ADMIN
