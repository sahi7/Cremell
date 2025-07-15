from django.urls import re_path
from notifications.consumers import *

websocket_urlpatterns = [
    re_path(r'ws/stakeholders/$', StakeholderConsumer.as_asgi()),
    re_path(r'^ws/kitchen/(?:(?P<branch_id>\d+)/)?$', KitchenConsumer.as_asgi()),
    re_path(r'ws/notifications/$', BranchConsumer.as_asgi()),
    re_path(r'ws/employee_updates/$', EmployeeUpdateConsumer.as_asgi()),
    # re_path(r'ws/assignments/$', consumers.AssignmentConsumer.as_asgi()),
]