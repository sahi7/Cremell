from django.urls import re_path
from notifications import consumers

websocket_urlpatterns = [
    re_path(r'ws/notifications/$', consumers.BranchConsumer.as_asgi()),
    re_path(r'ws/employee_updates/$', consumers.EmployeeUpdateConsumer.as_asgi()),
]