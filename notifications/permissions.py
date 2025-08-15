from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _


from .models import Task
from zMisc.utils import get_scopes_and_groups


class IsCookAndBranch(BasePermission):
    async def has_permission(self, request, view):
        user = request.user
        task_id = request.data.get('task_id')
        version = request.data.get('version')
        r_val = await user.get_role_value()

        if not task_id or version is None:
            return False
        
        # Check if user is authenticated and has 'cook' role
        if r_val < 1 or (r_val > 6 and r_val not in [7, 8, 9]):
            return False

        # Check if task's branch is in user's branches
        scopes = await get_scopes_and_groups(user)
        _branches = scopes['branches']

        try:
            # Fetch task with related order and branch
            task = await Task.objects.select_related('order').filter(
                id=task_id, version=version
            ).afirst()
            print("task: ",task)
            if not task:
                raise PermissionDenied(_("Task unavailable or modified."))
            
            # Check if task's branch is in user's branches
            if task.order.branch_id not in _branches:
                return False
            request.task = task

            return True
        except Task.DoesNotExist:
            return False