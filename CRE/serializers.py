from rest_framework import serializers
from allauth.account.adapter import get_adapter
from allauth.account.utils import setup_user_email
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
from .models import CustomUser

class CustomRegisterSerializer(RegisterSerializer):
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)
    gender = serializers.ChoiceField(choices=CustomUser.GENDER_CHOICES, required=False)
    date_of_birth = serializers.DateField(required=False)
    profile_picture = serializers.ImageField(required=False)
    phone_number = serializers.CharField(required=False)
    address_line_1 = serializers.CharField(required=False)
    address_line_2 = serializers.CharField(required=False)
    city = serializers.CharField(required=False)
    state = serializers.CharField(required=False)
    postal_code = serializers.CharField(required=False)
    country = serializers.CharField(required=True)
    role = serializers.ChoiceField(choices=CustomUser.ROLE_CHOICES, required=True)
    status = serializers.ChoiceField(choices=CustomUser.STATUS_CHOICES, required=False)
    salary = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    hire_date = serializers.DateField(required=False)
    bio = serializers.CharField(required=False)

    def get_cleaned_data(self):
        data_dict = super().get_cleaned_data()
        data_dict.update({
            'first_name': self.validated_data.get('first_name', ''),
            'last_name': self.validated_data.get('last_name', ''),
            'gender': self.validated_data.get('gender', ''),
            'date_of_birth': self.validated_data.get('date_of_birth', None),
            'profile_picture': self.validated_data.get('profile_picture', None),
            'phone_number': self.validated_data.get('phone_number', ''),
            'address_line_1': self.validated_data.get('address_line_1', ''),
            'address_line_2': self.validated_data.get('address_line_2', ''),
            'city': self.validated_data.get('city', ''),
            'state': self.validated_data.get('state', ''),
            'postal_code': self.validated_data.get('postal_code', ''),
            'country': self.validated_data.get('country', ''),
            'role': self.validated_data.get('role', ''),
            'status': self.validated_data.get('status', ''),
            'salary': self.validated_data.get('salary', None),
            'hire_date': self.validated_data.get('hire_date', None),
            'bio': self.validated_data.get('bio', ''),
        })
        return data_dict

    def save(self, request):
        adapter = get_adapter()
        user = adapter.new_user(request)
        self.cleaned_data = self.get_cleaned_data()
        adapter.save_user(request, user, self)
        setup_user_email(request, user, [])
        user.save()
        return user

class CustomUserDetailsSerializer(UserDetailsSerializer):
    class Meta(UserDetailsSerializer.Meta):
        model = CustomUser
        fields = (
            'pk', 'username', 'email', 'first_name', 'last_name', 'gender', 'date_of_birth', 'profile_picture',
            'is_active', 'phone_number', 'address_line_1', 'address_line_2', 'city', 'state', 'postal_code', 
            'country', 'role', 'status', 'salary', 'hire_date', 'bio'
        )
        read_only_fields = ('email', )
