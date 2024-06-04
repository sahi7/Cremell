from allauth.account.auth_backends import AuthenticationBackend
from django.contrib.auth import get_user_model

class CustomAuthBackend(AuthenticationBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        try:
            if '@' in username:
                user = UserModel.objects.get(email=username)
            elif username.isdigit():
                user = UserModel.objects.get(phone_number=username)
            else:
                user = UserModel.objects.get(username=username)
        except UserModel.DoesNotExist:
            return None
        
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def get_user(self, user_id):
        UserModel = get_user_model()
        try:
            return UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
