from .views import *
from django.urls import path, include
from rest_framework.routers import DefaultRouter

router = DefaultRouter()

router.register(r'transfers', TransferViewSet, basename='transfer')
router.register(r'transfer-history', TransferHistoryViewSet, basename='transfer-history') 

urlpatterns = [

    path('api/', include(router.urls)),

]
