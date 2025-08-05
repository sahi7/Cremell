import redis.asyncio as redis
from asgiref.sync import sync_to_async
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from CRE.models import Branch

CustomUser = get_user_model()

class BranchPermissionAss(BasePermission):
    async def has_permission(self, request, view):
        try:
            br = int(request.data['branches'][0])
            pk = view.kwargs.get('branch_id', br)
            if pk and pk != br:
                return False
            branch = await sync_to_async(Branch.objects.get)(id=pk)
            request.branch = branch
        except (KeyError, TypeError):
            raise PermissionDenied({'branch': _('branch must be specified.')})  
        except Branch.DoesNotExist:
            raise PermissionDenied({'branch': _('not found.')})  
        
        view_name = view.__class__.__name__
        user = request.user
        if view_name == 'BranchPermissionPoolView':
            if request.method != 'GET':
                if not await user.get_role_value() <=4:
                    return False

        return True