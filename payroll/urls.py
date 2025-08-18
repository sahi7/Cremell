from .views import *
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from adrf.routers import SimpleRouter

router = DefaultRouter()
arouter = SimpleRouter()

router.register(r'rules', RuleViewSet, basename='rule')

urlpatterns = [
    path('payroll/', include(router.urls)),
    path('payroll/overrides/', OverrideCreateView.as_view(), name='override-create'),
    path('payroll/generate/', GeneratePayrollView.as_view(), name='generate-payroll'),
    path('payroll/payslip/', PayslipView.as_view(), name='payslip'),
]