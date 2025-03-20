from .views import *
from rest_framework.routers import DefaultRouter

router = DefaultRouter()


router.register(r'transfers', views.TransferViewSet, basename='transfer')
router.register(r'transfer-history', views.TransferHistoryViewSet, basename='transfer-history')
