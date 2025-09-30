from django.urls import path, include
from rest_framework.routers import DefaultRouter
from django.utils.translation import gettext_lazy as _

from .views import PlanViewSet, FeatureViewSet, CouponViewSet, CouponRedemptionView

router = DefaultRouter()
router.register(r'plans', PlanViewSet, basename='plan')
router.register(r'features', FeatureViewSet, basename='feature')
router.register(r'coupons', CouponViewSet, basename='coupon')

urlpatterns = [
    path('', include(router.urls)),
    path('coupons/redeem/', CouponRedemptionView.as_view({'post': 'create'}), name='coupon-redeem'),
]