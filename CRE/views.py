from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from django.db.models import Q

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import ModelViewSet
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework_simplejwt.views import TokenBlacklistView

from dj_rest_auth.registration.views import SocialLoginView
from dj_rest_auth.registration.views import RegisterView
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client

from rest_access_policy import AccessViewSetMixin

from .serializers import UserSerializer, CustomRegisterSerializer, RegistrationSerializer, RestaurantSerializer, BranchSerializer, BranchMenuSerializer, MenuSerializer, MenuCategorySerializer
from .serializers import MenuItemSerializer, CompanySerializer
from zMisc.policies import UserAccessPolicy, RestaurantAccessPolicy, BranchAccessPolicy
from zMisc.permissions import UserCreationPermission, RManagerScopePermission, BManagerScopePermission, ObjectStatusPermission
from zMisc.utils import validate_scope, filter_queryset_by_scopes
from .models import Restaurant, Branch, Menu, MenuItem, MenuCategory

CustomUser = get_user_model()
def email_confirm_redirect(request, key):
    return HttpResponseRedirect(
        f"{settings.EMAIL_CONFIRM_REDIRECT_BASE_URL}{key}/"
    )


def password_reset_confirm_redirect(request, uidb64, token):
    return HttpResponseRedirect(
        f"{settings.PASSWORD_RESET_CONFIRM_REDIRECT_BASE_URL}{uidb64}/{token}/"
    )


class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    callback_url = "http://localhost:8000/"
    client_class = OAuth2Client


class LogoutView(TokenBlacklistView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            return Response({"detail": _("Successfully logged out")}, status=status.HTTP_200_OK)
        return response

class CustomRegisterView(RegisterView):
    serializer_class = CustomRegisterSerializer


class RegistrationView(APIView):
    """
    Handle registration for both single restaurants and companies.
    """
    permission_classes = [AllowAny]
    def post(self, request, *args, **kwargs):
        serializer = RegistrationSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            # Create either company or restaurant based on the data
            user_type = 'company' if 'company_data' in request.data else 'restaurant'
            serializer.context['role'] = 'company_admin' if user_type == 'company' else 'restaurant_owner'
            
            # Create and return the user/restaurant/company
            instance = serializer.save()
            return Response({"message": _("Registration successful!")}, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserViewSet(ModelViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = UserSerializer
    permission_classes = (UserAccessPolicy, UserCreationPermission, )

    def get_queryset(self):
        user = self.request.user

        # Company Admin: Return users within the same company
        if user.groups.filter(name="CompanyAdmin").exists():
            return CustomUser.objects.filter(companies__in=user.companies.all())

        # Restaurant Owner: Return users associated with their restaurants (including standalone ones)
        elif user.groups.filter(name="RestaurantOwner").exists():
            return CustomUser.objects.filter(
                restaurants__in=user.restaurants.all()
            ).filter(
                Q(companies__in=user.companies.all()) | Q(companies__isnull=True)
            )

        # Country Manager: Return users in the same country (and same company if applicable)
        elif user.groups.filter(name="CountryManager").exists():
            return CustomUser.objects.filter(
                countries__in=user.countries.all(), 
                companies__in=user.companies.all()  # Ensures they only see users from their company in the same country
            )

        # Restaurant Manager: Return users associated with their managed restaurants (and same company if applicable)
        elif user.groups.filter(name="RestaurantManager").exists():
            return CustomUser.objects.filter(
                restaurants__manager=user, 
                companies__in=user.companies.all()  # Ensures they only see users from their company managing the same restaurants
            ).distinct()

        # Default: Return all users (if no specific group matched, this could be adjusted as needed)
        return CustomUser.objects.none()

    def create(self, request, *args, **kwargs):
        user = self.request.user
        role_to_create = request.data.get('role')

        # Compare roles: Only allow the creation of a user with a lower role unless the user is a CompanyAdmin
        if user.groups.filter(name="CompanyAdmin").exists():
            # CompanyAdmin can create any user
            pass
        else:
            user_role_value = user.get_role_value()
            role_to_create_value = user.get_role_value(role_to_create)

            # Restrict role creation: New user’s role must be lower than the creator’s role
            if role_to_create_value <= user_role_value:
                return Response({"detail": _("You cannot create a user with a higher or equal role.")},
                                status=status.HTTP_400_BAD_REQUEST)

        context = self.get_serializer_context()
        context['role'] = role_to_create
         # Create the serializer instance with the role in the context
        serializer = self.get_serializer(data=request.data, context=context)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # After saving the user, we can add the user to the appropriate group based on their role
        user.add_to_group(role_to_create)

        return Response(serializer.data, status=status.HTTP_201_CREATED)

class RestaurantViewSet(ModelViewSet):
    queryset = Restaurant.objects.all()
    serializer_class = RestaurantSerializer
    permission_classes = (RestaurantAccessPolicy, RManagerScopePermission, ObjectStatusPermission)

    # Custom action to list all branches of a restaurant
    @action(detail=True, methods=['get'])
    def branches(self, request, pk=None):
        restaurant = self.get_object()
        branches = restaurant.branches.all()
        serializer = BranchSerializer(branches, many=True)
        return Response(serializer.data)

    # Custom action to list all employees of a restaurant
    @action(detail=True, methods=['get'])
    def employees(self, request, pk=None):
        restaurant = self.get_object()
        employees = restaurant.employees.all()
        serializer = UserSerializer(employees, many=True)
        return Response(serializer.data)

    # Custom action to retrieve the company that owns the restaurant
    @action(detail=True, methods=['get'])
    def company(self, request, pk=None):
        restaurant = self.get_object()
        company = restaurant.company
        if company:
            serializer = CompanySerializer(company)
            return Response(serializer.data)
        else:
            return Response({"detail": "This restaurant is not associated with any company."}, status=status.HTTP_404_NOT_FOUND)

    def get_queryset(self):
        user = self.request.user

        # Company Admin: View all restaurants under their company
        if user.groups.filter(name="CompanyAdmin").exists():
            return Restaurant.objects.filter(company__in=user.companies.all())

        # Country Manager: View restaurants in their country for their company
        elif user.groups.filter(name="CountryManager").exists():
            return Restaurant.objects.filter(
                country__in=user.countries.all(),
                company__in=user.companies.all()
            )

        # Restaurant Owner: View restaurants they own
        elif user.groups.filter(name="RestaurantOwner").exists():
            return Restaurant.objects.filter(
                Q(created_by=user) 
            ).distinct()

        # Restaurant Manager: View restaurants they manage
        elif user.groups.filter(name="RestaurantManager").exists():
            return Restaurant.objects.filter(manager__in=[user]).distinct()

        # Default: No access for other roles
        return Restaurant.objects.none()

    def create(self, request, *args, **kwargs):
        user = request.user

        # Define allowed scopes for the user
        allowed_scopes = {}

        # Get data from the request
        data = request.data

        # Validation for role-based creation permissions
        if user.groups.filter(name="CompanyAdmin").exists():
            allowed_scopes['company'] = user.companies.values_list('id', flat=True)
            

        elif user.groups.filter(name="CountryManager").exists():
            # CountryManager: Restricted by country and company
            allowed_scopes['country'] = user.countries.values_list('id', flat=True)
            allowed_scopes['company'] = user.companies.values_list('id', flat=True)

        else:
            # Other roles cannot create restaurants
            return Response({"detail": _("You do not have permission to create a restaurant.")},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            validate_scope(user, data, allowed_scopes)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

        # Pass data to serializer and save
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        restaurant = serializer.save(created_by=user)

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BranchViewSet(ModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    permission_classes = (BranchAccessPolicy, ObjectStatusPermission, BManagerScopePermission,)

    def get_queryset(self):
        user = self.request.user
        allowed_scopes = {}
        # print(user.groups.all())
        # Define allowed scopes with complex filters for each user role
        if user.groups.filter(name="CountryManager").exists():
            allowed_scopes = {
                'country': Q(country__in=user.countries.all()),  # Only branches in the user's countries
                'company': Q(company__in=user.companies.all()),  # Only branches in the user's companies
            }
        elif user.groups.filter(name="CompanyAdmin").exists():
            allowed_scopes = {
                'company': Q(company__in=user.companies.all()),  # CompanyAdmin can see all branches in their company
            }
        elif user.groups.filter(name="RestaurantOwner").exists() or user.groups.filter(name="BranchOwner").exists():
            allowed_scopes = {
                'restaurants': Q(created_by=user) | Q(manager=user),  # RestaurantOwner can only see their own branches
            }
        elif user.groups.filter(name="RestaurantManager").exists():
            allowed_scopes = {
                'restaurants': Q(manager=user),  # RestaurantManager can only see branches they manage
                'status': Q(status="active"),  # Only active branches for RestaurantManager
                'company': Q(company__in=user.companies.all())  # Only branches in the user's company
            }

        # Apply filtering with the reusable method
        try:
            queryset = filter_queryset_by_scopes(self.queryset, user, allowed_scopes)
        except PermissionDenied:
            raise PermissionDenied(_("You do not have permission to access this branch."))

        return queryset

    def perform_create(self, serializer):
        user = self.request.user

        # Define allowed scopes for the user
        allowed_scopes = {}

        # Get data from the request
        data = self.request.data

        # Validation for role-based creation permissions
        if user.groups.filter(name="CompanyAdmin").exists():
            allowed_scopes['company'] = user.companies.values_list('id', flat=True)
            allowed_scopes['restaurant'] = user.restaurants.values_list('id', flat=True)


        elif user.groups.filter(name="CountryManager").exists():
            # CountryManager: Restricted by country and company
            allowed_scopes['country'] = user.countries.values_list('id', flat=True)
            allowed_scopes['company'] = user.companies.values_list('id', flat=True)
            allowed_scopes['restaurant'] = user.restaurants.values_list('id', flat=True)

        elif user.groups.filter(name="RestaurantOwner").exists():
            allowed_scopes['restaurant'] = user.restaurants.values_list('id', flat=True)
            allowed_scopes['country'] = user.countries.values_list('id', flat=True)

        else:
            # Other roles cannot create restaurants
            return Response({"detail": _("You do not have permission to create a branch.")},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            # print("Allowed Scopes:", allowed_scopes)
            validate_scope(user, data, allowed_scopes)
            print("Scope validated successfully")
        except ValidationError as e:
            # print("Scope validation failed:", e.detail)
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()

class MenuViewSet(ModelViewSet):
    queryset = Menu.objects.all()
    serializer_class = MenuSerializer

class MenuCategoryViewSet(ModelViewSet):
    queryset = MenuCategory.objects.all()
    serializer_class = MenuCategorySerializer

class MenuItemViewSet(ModelViewSet):
    queryset = MenuItem.objects.all()
    serializer_class = MenuItemSerializer

class BranchMenuDetailView(APIView):
    def get(self, request, branch_id=None, pk=None):
        if branch_id and pk:
            # Fetch the specific menu for the given branch and pk
            branch = get_object_or_404(Branch, id=branch_id)
            menu = get_object_or_404(Menu, id=pk, branch=branch)  # Assuming a relationship between menu and branch
            categories = MenuCategory.objects.filter(menu=menu).prefetch_related('menu_items')
            data = {
                "menu": MenuSerializer(menu).data,
                "categories": MenuCategorySerializer(categories, many=True).data
            }
            return Response(data)
        
        elif branch_id:
            # Fetch all menus for the given branch
            branch = get_object_or_404(Branch, id=branch_id)
            menus = Menu.objects.filter(branch=branch)
            serializer = BranchMenuSerializer(menus, many=True)
            return Response(serializer.data)
        
        return Response({"detail": _("Invalid request")}, status=400)


