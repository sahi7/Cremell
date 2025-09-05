from django.urls import re_path
from notifications.consumers import *

websocket_urlpatterns = [
    re_path(r'ws/stakeholders/$', StakeholderConsumer.as_asgi()),
    re_path(r'^ws/kitchen/(?:(?P<branch_id>\d+)/)?$', KitchenConsumer.as_asgi()),
    re_path(r'^ws/notifications/(?:(?P<branch_id>\d+)/)?$', BranchConsumer.as_asgi()),
    re_path(r"^ws/device/(?P<device_id>[0-9A-Za-z]+)/$", HardwareConsumer.as_asgi()),
    re_path(r'ws/employee_updates/$', EmployeeUpdateConsumer.as_asgi()),
    # re_path(r'ws/assignments/$', consumers.AssignmentConsumer.as_asgi()),
]