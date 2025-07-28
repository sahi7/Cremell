from rest_framework.permissions import BasePermission
from django.contrib.auth import get_user_model
from .models import Rule
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
        target_cat = target.get('category')
        # print("target: ", target_type, target_value, target_cat)

        if target_type == 'role':
            if await compare_role_values(request.user, target_value):
                return False
        elif target_type == 'user':
            entity_permission = EntityUpdatePermission()
            try:
                if not target_cat:
                    target_user = await CustomUser.objects.aget(id=target_value)
                    target_scope = CustomUser
                else:
                    target_user = await Rule.objects.aget(id=target_value)
                    target_scope = Rule
                return await entity_permission._is_object_in_scope(request, target_user, target_scope)
            except CustomUser.DoesNotExist:
                return False
            except Rule.DoesNotExist:
                return False
        return True

    async def has_permission(self, request, view):
        data = request.data
        targets = data.get('targets')
        if targets:
            for target in targets:
                return await self.check_target_permission(request, target)
        view_name = view.__class__.__name__
        if view_name == 'OverrideCreateView':
            rule = data.get('rule')
            user = data.get('user')
            target = {"target_type": "user", "target_value": user}
            rule = {"target_type": "user", "target_value": rule, "category": "rule"}

            if not await self.check_target_permission(request, target):
                return False
            
            if not await self.check_target_permission(request, rule):
                return False
            
        return True