from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

from django.contrib.auth import get_user_model
from zMisc.permissions import EntityUpdatePermission

CustomUser = get_user_model()

class DevicePermission(BasePermission):
    async def has_permission(self, request, view):
        entity_permission = EntityUpdatePermission()
        user_id = request.data.get('user')
        if user_id:
            try:
                user = await CustomUser.objects.aget(id=user_id)
                return await entity_permission._is_object_in_scope(request, user, CustomUser)
            except CustomUser.DoesNotExist:
                return False
        
        return True