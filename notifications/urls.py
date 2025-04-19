from .views import *
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from adrf.routers import SimpleRouter

router = DefaultRouter()
arouter = SimpleRouter()

router.register(r'transfers', TransferViewSet, basename='transfer')
router.register(r'transfer-history', TransferHistoryViewSet, basename='transfer-history') 
arouter.register(r'role-assignment', RoleAssignmentViewSet, basename='role-assignment')

urlpatterns = [

    path('n/', include(router.urls)),
    path('a/', include(arouter.urls)),

]
