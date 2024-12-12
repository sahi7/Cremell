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
from zMisc.utils import check_user_role
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
        if self.request.user.role == 'restaurant_owner':  # RestaurantOwner
            return CustomUser.objects.filter(restaurants__in=self.request.user.restaurants.all())
        elif self.request.user.role == 'country_manager':  # CountryManager
            return CustomUser.objects.filter(country=self.request.user.country)
        return super().get_queryset()

class RestaurantViewSet(AccessViewSetMixin, ModelViewSet):
    queryset = Restaurant.objects.all()
    serializer_class = RestaurantSerializer
    access_policy = RestaurantAccessPolicy


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

        if user.groups.filter(name="CountryManager").exists() and user.country:
            return Branch.objects.filter(country=user.country)

        if user.groups.filter(name="RestaurantManager").exists():
            return Branch.objects.filter(restaurant__created_by=user)

        if user.groups.filter(name="RestaurantOwner").exists():
            return Branch.objects.filter(restaurant__created_by=user)

        return Branch.objects.none()