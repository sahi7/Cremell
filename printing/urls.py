from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import *

router = DefaultRouter()
router.register(r'devices', DeviceViewSet, basename='device')
# POST /api/hardware/devices/{id}/reset/: Remove all printers
# POST /api/hardware/devices/{id}/scan-printers/: Scan for printers

urlpatterns = [
    path('hardware/', include(router.urls)),
    path('hardware/register-device/', RegisterDeviceView.as_view(), name='register_device'),
]