# CRE/urls.py 

from dj_rest_auth.registration.views import RegisterView, ResendEmailVerificationView, VerifyEmailView
from dj_rest_auth.views import LoginView, UserDetailsView, PasswordResetConfirmView, PasswordResetView
from rest_framework_simplejwt.views import TokenRefreshView, TokenObtainPairView
from CRE.views import email_confirm_redirect, password_reset_confirm_redirect
from django.urls import path
from .views import LogoutView, CustomRegisterView

from allauth.socialaccount.views import signup
from CRE.views import GoogleLogin

urlpatterns = [
    # path("register/", RegisterView.as_view(), name="rest_register"),
    path("register/", CustomRegisterView.as_view(), name='custom_registration'),
    path("login/", LoginView.as_view(), name="rest_login"),
    # path("logout/", LogoutView.as_view(), name="rest_logout"),
    path('logout/', LogoutView.as_view(), name='logout'), # parse refresh_token via POST
    path("user/", UserDetailsView.as_view(), name="rest_user_details"),

    # JWT tokens
    path('token/', TokenObtainPairView.as_view(), name='token_obtain_pair'), # View token
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    path("register/verify-email/", VerifyEmailView.as_view(), name="rest_verify_email"),
    path("register/resend-email/", ResendEmailVerificationView.as_view(), name="rest_resend_email"),
    path("account-confirm-email/<str:key>/", email_confirm_redirect, name="account_confirm_email"),
    path("account-confirm-email/", VerifyEmailView.as_view(), name="account_email_verification_sent"),
    path("password/reset/", PasswordResetView.as_view(), name="rest_password_reset"),
    path(
        "password/reset/confirm/<str:uidb64>/<str:token>/",
        password_reset_confirm_redirect,
        name="password_reset_confirm",
    ),
    path("password/reset/confirm/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),

    # Social auth 
    path("signup/", signup, name="socialaccount_signup"),
    path("google/", GoogleLogin.as_view(), name="google_login"),
]