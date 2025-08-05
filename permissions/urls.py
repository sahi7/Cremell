from django.urls import path
from .views import *

urlpatterns = [
    path('branches/<int:branch_id>/permissions/assign/', BranchPermissionAssignmentView.as_view(), name='permission-assignment'),
    path('branches/<int:branch_id>/permissions/pool/', BranchPermissionPoolView.as_view(), name='permission-pool'),
    path('global/permissions/', globalPermissionPool.as_view(), name='global-pool')
]