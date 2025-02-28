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
from CRE.views__helper import CheckUserExistsView, UserScopeView
from CRE.views import *
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

router.register(r'menus', MenuViewSet)
router.register(r'menu-categories', MenuCategoryViewSet)
router.register(r'menu-items', MenuItemViewSet)

def api_path(route, view, name=None):
    return path(f'api/{route}', view, name=name)

urlpatterns = [
    path('api/auth/', include('CRE.urls')),
    path('admin/', admin.site.urls),

    # allauth
    path('accounts/', include('allauth.urls')),

    # CRE 
    api_path('register/', RegistrationView.as_view(), name='user-register'),
    api_path('check-user/', CheckUserExistsView.as_view(), name='check-user'),
    api_path('user-scope/', UserScopeView.as_view(), name='user-scope'),
    api_path('orders/<int:order_id>/modify/', OrderModifyView.as_view(), name='order-modify'),
    path('api/', include(router.urls)),
]
