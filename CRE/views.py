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

from dj_rest_auth.registration.views import SocialLoginView
from dj_rest_auth.registration.views import RegisterView
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client

from .serializers import UserSerializer, CustomRegisterSerializer, RegistrationSerializer
from zMisc.policies import UserAccessPolicy
from zMisc.utils import check_user_role

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
    access_policy_class = UserAccessPolicy

    def get_queryset(self):

        # Deny access if the role value is greater than 4
        check_user_role(self.request.user)

        # Enforce user-specific filtering
        if self.request.user.role == 'restaurant_owner':  # RestaurantOwner
            return CustomUser.objects.filter(restaurants__in=self.request.user.restaurants.all())
        elif self.request.user.role == 'country_manager':  # CountryManager
            return CustomUser.objects.filter(country=self.request.user.country)
        return super().get_queryset()
