from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

class DevicePermission(BasePermission):
    async def has_permission(self, request, view):
        pass