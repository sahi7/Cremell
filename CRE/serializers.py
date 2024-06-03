# serializers.py
from rest_framework import serializers
from allauth.account.adapter import get_adapter
from allauth.account.utils import setup_user_email
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
from .models import CustomUser

class CustomRegisterSerializer(RegisterSerializer):
    gender = serializers.ChoiceField(choices=CustomUser.GENDER_CHOICES, required=False, allow_blank=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    profile_picture = serializers.ImageField(required=False, allow_null=True)
    phone_number = serializers.CharField(required=False, allow_blank=True)
    address_line_1 = serializers.CharField(required=False, allow_blank=True)
    address_line_2 = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(required=False, allow_blank=True)
    state = serializers.CharField(required=False, allow_blank=True)
    postal_code = serializers.CharField(required=False, allow_blank=True)
    country = serializers.CharField(required=True)
    status = serializers.ChoiceField(choices=CustomUser.STATUS_CHOICES, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=CustomUser.ROLE_CHOICES, required=False, allow_blank=True)
    salary = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    hire_date = serializers.DateField(required=False, allow_null=True)
    bio = serializers.CharField(required=False, allow_blank=True)

    def get_cleaned_data(self):
        data_dict = super().get_cleaned_data()
        data_dict.update({
            'gender': self.validated_data.get('gender', ''),
            'date_of_birth': self.validated_data.get('date_of_birth', ''),
            'profile_picture': self.validated_data.get('profile_picture', ''),
            'phone_number': self.validated_data.get('phone_number', ''),
            'address_line_1': self.validated_data.get('address_line_1', ''),
            'address_line_2': self.validated_data.get('address_line_2', ''),
            'city': self.validated_data.get('city', ''),
            'state': self.validated_data.get('state', ''),
            'postal_code': self.validated_data.get('postal_code', ''),
            'country': self.validated_data.get('country', ''),
            'status': self.validated_data.get('status', ''),
            'role': self.validated_data.get('role', ''),
            'salary': self.validated_data.get('salary', ''),
            'hire_date': self.validated_data.get('hire_date', ''),
            'bio': self.validated_data.get('bio', ''),
        })
        return data_dict

    def save(self, request):
        user = super().save(request)
        user.gender = self.validated_data.get('gender', '')
        user.date_of_birth = self.validated_data.get('date_of_birth', '')
        user.profile_picture = self.validated_data.get('profile_picture', '')
        user.phone_number = self.validated_data.get('phone_number', '')
        user.address_line_1 = self.validated_data.get('address_line_1', '')
        user.address_line_2 = self.validated_data.get('address_line_2', '')
        user.city = self.validated_data.get('city', '')
        user.state = self.validated_data.get('state', '')
        user.postal_code = self.validated_data.get('postal_code', '')
        user.country = self.validated_data.get('country', '')
        user.status = self.validated_data.get('status', '')
        user.role = self.validated_data.get('role', '')
        user.salary = self.validated_data.get('salary', '')
        user.hire_date = self.validated_data.get('hire_date', '')
        user.bio = self.validated_data.get('bio', '')
        user.save()
        return user

class CustomUserDetailsSerializer(UserDetailsSerializer):
    class Meta(UserDetailsSerializer.Meta):
        model = CustomUser
        fields = (
            'pk', 'username', 'email', 'first_name', 'last_name', 'gender', 'date_of_birth', 'profile_picture',
            'phone_number', 'address_line_1', 'address_line_2', 'city', 'state', 'postal_code', 'country',
            'status', 'role', 'salary', 'hire_date', 'bio'
        )
        read_only_fields = ('email',)
