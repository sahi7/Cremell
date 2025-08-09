"""
URL configuration for Carousel project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from cre.views__helper import CheckUserExistsView, UserScopeView, AssignmentView
from cre.views import *
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'branches', BranchViewSet)
# - GET /api/branches/{id}/menus/: List all menus for a branch.
# - GET /api/branches/{id}/menus/{menu_id}/: Retrieve details of a specific menu for a branch.

router.register(r'restaurants', RestaurantViewSet)
# custom actions will generate the following additional routes:
#  - GET /api/restaurants/{id}/branches/: List all branches of a restaurant.
#  - GET /api/restaurants/{id}/employees/: List all employees of a restaurant.
#  - GET /api/restaurants/{id}/company/: Retrieve the company that owns the restaurant.

router.register(r'companies', CompanyViewSet, basename='company')
# - GET /api/companies/stats/ - Custom stats endpoint 

router.register(r'menus', MenuViewSet)
router.register(r'menu-categories', MenuCategoryViewSet)
router.register(r'menu-items', MenuItemViewSet)

router.register(r'shifts', ShiftViewSet)
router.register(r"staff-shifts", StaffShiftViewSet, basename="staff-shift")
#  - POST /api/staff-shifts/{id}/reassign/: Emergency overide on a staff shift.
router.register(r"shift-swaps", ShiftSwapRequestViewSet, basename="shift-swap")
#  - POST /api/shift-swaps/{id}/accept/: Accept shift swap requests.

router.register(r"shift-patterns", ShiftPatternViewSet, basename="shift-pattern")
#  - POST /api/shift-patterns/{id}/regenerate/: Generate staff shift(s) for the shift-pattern id.

router.register(r"overtime-request", OvertimeRequestViewSet, basename="ot-request")
#  - POST /api/overtime-request/{id}/approve/: For admin to approve ot_request

router.register(r"orders", OrderViewSet)
# POST /api/orders/{id}/modify/: Modify orders
# POST /api/orders/{id}/cancel/: Cancel orders

def api_path(route, view, name=None):
    return path(f'api/{route}', view, name=name)

urlpatterns = [
    path('api/auth/', include('cre.urls')),
    path('admin/', admin.site.urls),

    # allauth
    path('accounts/', include('allauth.urls')),

    # cre 
    api_path('register/', RegistrationView.as_view(), name='user-register'),
    api_path('check-user/', CheckUserExistsView.as_view(), name='check-user'),
    api_path('user-scope/', UserScopeView.as_view(), name='user-scope'),
    api_path('assignments/', AssignmentView.as_view(), name='assignment-update'),
    path('api/', include(router.urls)),
    path('api/', include('notifications.urls')),
    path('api/', include('payroll.urls')),
    path('api/', include('permissions.urls')),
]
