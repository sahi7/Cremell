from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import ModelViewSet
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.views import TokenBlacklistView

from dj_rest_auth.registration.views import SocialLoginView
from dj_rest_auth.registration.views import RegisterView
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client

from rest_access_policy import AccessViewSetMixin

from .serializers import UserSerializer, CustomRegisterSerializer, RegistrationSerializer, RestaurantSerializer, BranchSerializer
from zMisc.policies import UserAccessPolicy, RestaurantAccessPolicy, BranchAccessPolicy
from .models import Restaurant, Branch

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

class CheckUserExistsView(APIView):
    """
    API View to check if a user exists based on email or phone number.
    """

    def get(self, request, *args, **kwargs):
        email = request.query_params.get('email')
        phone_number = request.query_params.get('phone_number')

        if not email and not phone_number:
            return Response(
                {"detail": _("Please provide either 'email' or 'phone_number' as a query parameter.")},
                status=status.HTTP_400_BAD_REQUEST
            )

        user_exists = CustomUser.objects.filter(
            email=email if email else None,
            phone_number=phone_number if phone_number else None
        ).exists()

        return Response({"user_exists": user_exists}, status=status.HTTP_200_OK)


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
    permission_classes = (UserAccessPolicy, )

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

class RestaurantViewSet(AccessViewSetMixin, ModelViewSet):
    queryset = Restaurant.objects.all()
    serializer_class = RestaurantSerializer
    access_policy = RestaurantAccessPolicy

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

        # Get data from the request
        data = request.data

        # Validation for role-based creation permissions
        if user.groups.filter(name="CompanyAdmin").exists():
            # CompanyAdmin can create restaurants globally
            pass  # No restrictions for CompanyAdmin

        elif user.groups.filter(name="CountryManager").exists():
            # Restrict to the country assigned to the CountryManager
            if data.get('country') not in user.countries.values_list('id', flat=True):
                return Response({"detail": _("You cannot create restaurants outside your assigned countries.")},
                                status=status.HTTP_400_BAD_REQUEST)

        else:
            # Other roles cannot create restaurants
            return Response({"detail": _("You do not have permission to create a restaurant.")},
                            status=status.HTTP_403_FORBIDDEN)

        # Pass data to serializer and save
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        restaurant = serializer.save(created_by=user)

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BranchViewSet(AccessViewSetMixin, ModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    access_policy = BranchAccessPolicy

    def perform_create(self, serializer):
        # Here, we make sure that the authenticated user can create a branch
        # and set the current restaurant for the branch
        restaurant = self.request.data.get('restaurant')
        if not Restaurant.objects.filter(id=restaurant).exists():
            raise serializers.ValidationError("Invalid restaurant ID.")
        serializer.save(created_by=self.request.user)

    def get_queryset(self):
        user = self.request.user

        if user.groups.filter(name="CompanyAdmin").exists() and user.company:
            return Branch.objects.filter(company=user.company)
        
        if user.groups.filter(name="CountryManager").exists():
            return Branch.objects.filter(restaurant__country=user.country)

        if user.groups.filter(name="RestaurantManager").exists():
            return Branch.objects.filter(restaurant__manager=user)

        if user.groups.filter(name="RestaurantOwner").exists():
            return Branch.objects.filter(restaurant__created_by=user)

        return Branch.objects.none()