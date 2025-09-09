from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import *

router = DefaultRouter()
router.register(r'devices', DeviceViewSet, basename='device')
# POST /api/hardware/devices/{id}/reset/: Remove all printers
# POST /api/hardware/devices/{id}/scan-printers/: Scan for printers
# POST /api/hardware/devices/{id}/remove-printer/ : Remove a specific printer from device
# POST /api/hardware/devices/{id}/set-default-printer/ 
# POST /api/hardware/devices/{id}/list-printers/: Get all

urlpatterns = [
    path('hardware/', include(router.urls)),
    path('hardware/register-device/', RegisterDeviceView.as_view(), name='register_device'),
]