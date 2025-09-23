from .views import *
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from adrf.routers import SimpleRouter

router = DefaultRouter()
arouter = SimpleRouter()

router.register(r'rules', RuleViewSet, basename='rule')
router.register(r'overrides', OverrideViewSet, basename='override')
router.register(r'payslips', PayslipViewSet, basename='payslip')

urlpatterns = [
    path('payroll/', include(router.urls)),
    path('payroll/generate/', GeneratePayrollView.as_view(), name='generate-payroll'),
]