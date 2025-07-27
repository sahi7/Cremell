from django.urls import path
from .views import RuleCreateView, OverrideCreateView, GeneratePayrollView, PayslipView

urlpatterns = [
    path('rules/', RuleCreateView.as_view(), name='rule-create'),
    path('overrides/', OverrideCreateView.as_view(), name='override-create'),
    path('generate-payroll/', GeneratePayrollView.as_view(), name='generate-payroll'),
    path('payslip/', PayslipView.as_view(), name='payslip'),
]