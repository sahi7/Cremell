from django.urls import path
from .views import *

urlpatterns = [
    path('branches/<int:branch_id>/permissions/assign/', BranchPermissionAssignmentView.as_view(), name='permission-assignment'),
    path('branches/<int:branch_id>/permissions/pool/', BranchPermissionPoolView.as_view(), name='permission-pool'),
    path('branches/<int:branch_id>/permissions/me/', UserPermissionView.as_view(), name='user-permissions-branch'),
    path('permissions/me/', UserPermissionView.as_view(), name='user-permissions'),
    path('global/permissions/', globalPermissionPool.as_view(), name='global-pool'),
]