from rest_framework.permissions import BasePermission
from django.contrib.auth import get_user_model
from zMisc.utils import get_scopes_and_groups, compare_role_values
from zMisc.permissions import EntityUpdatePermission

CustomUser = get_user_model()

class RulePermission(BasePermission):
    async def check_target_permission(self, request, target):
        """
        Checks if the target (role or user) is valid and within the request's scope.
        Efficiently handles role or user target types with appropriate checks.
        """
        target_type = target.get('target_type')
        target_value = target.get('target_value')
        # print("target: ", target_type, target_value)

        if target_type == 'role':
            if await compare_role_values(request.user, target_value):
                return False
        elif target_type == 'user':
            entity_permission = EntityUpdatePermission()
            try:
                target_user = await CustomUser.objects.aget(id=target_value)
                return await entity_permission._is_object_in_scope(request, target_user, CustomUser)
            except CustomUser.DoesNotExist:
                return False
        return True

    async def has_permission(self, request, view):
        data = request.data
        targets = data.get('targets')
        if targets:
            for target in targets:
                return await self.check_target_permission(request, target)
        return True