from rest_framework import permissions
from django.utils.translation import gettext_lazy as _

class IsSuperUser(permissions.BasePermission):
    message = _('Only superusers can perform this action.')

    async def has_permission(self, request, view):
        return request.user and await sync_to_async(lambda: request.user.is_superuser)()