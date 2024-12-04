from rest_framework import serializers
from allauth.account.adapter import get_adapter
from allauth.account.utils import setup_user_email
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
from .models import CustomUser, Company, Restaurant, City, Country, RegionOrState
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.password_validation import validate_password

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

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    role = serializers.CharField(read_only=True)  # This will be assigned in the view or user manager

    class Meta:
        model = CustomUser
        fields = ['email', 'phone_number', 'password', 'role', 'first_name', 'last_name']

    def create(self, validated_data):
        # Assign role and use custom manager method to create the user
        role = validated_data.get('role')
        email = validated_data.get('email')
        phone_number = validated_data.get('phone_number')
        password = validated_data.get('password')
        
        # Use the create_user_with_role method to create the user with the role
        user = CustomUser.objects.create_user_with_role(role, email, phone_number, password)
        
        return user

class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = ['id', 'name', 'iso_code', 'currency', 'timezone', 'language']

class RegionOrStateSerializer(serializers.ModelSerializer):
    country = CountrySerializer()  # This serializer will link to the Country model via a Foreign Key relationship

    class Meta:
        model = RegionOrState
        fields = ['id', 'country', 'name', 'type']

class CitySerializer(serializers.ModelSerializer):
    region_or_state = RegionOrStateSerializer() 

    class Meta:
        model = City
        fields = ['id', 'region_or_state', 'name', 'postal_code']

# Company registration serializer
class CompanySerializer(serializers.ModelSerializer):
    user = UserSerializer()
    name = serializers.CharField(max_length=255)
    about = serializers.CharField(max_length=1000, required=False)
    contact_email = serializers.EmailField(required=False)
    contact_phone = serializers.CharField(max_length=15, required=False)
    
    class Meta:
        model = Company
        fields = ['name', 'about', 'contact_email', 'contact_phone', 'user']

    def create(self, validated_data):
        user_data = validated_data.pop('user')
        company = Company.objects.create(**validated_data)  # Create company

        # Create user as company admin
        user = CustomUser.objects.create_user_with_role('company_admin', user_data['email'], 
                                                         user_data['phone_number'], user_data['password'])
        # Create the first restaurant (main branch) for the company
        restaurant = Restaurant.objects.create(
            name=f"{company.name} Main Restaurant",
            company=company,
            address=validated_data.get('about', ''),
            # city=validated_data.get('contact_email', ''),  # example fields, adapt as necessary
            # country=validated_data.get('contact_phone', '')  # example fields, adapt as necessary
        )

        # Set the user as the creator of the restaurant
        restaurant.created_by = user
        restaurant.save()

        return company

# Restaurant registration serializer
class RestaurantSerializer(serializers.ModelSerializer):
    user = UserSerializer()
    name = serializers.CharField(max_length=255)
    address = serializers.CharField(max_length=1000)
    city = serializers.PrimaryKeyRelatedField(queryset=City.objects.all())
    country = serializers.PrimaryKeyRelatedField(queryset=Country.objects.all())

    class Meta:
        model = Restaurant
        fields = ['name', 'address', 'city', 'country', 'user']

    def create(self, validated_data):
        user_data = validated_data.pop('user')
        
        # Create the user as restaurant owner
        user = CustomUser.objects.create_user_with_role('restaurant_owner', user_data['email'], 
                                                         user_data['phone_number'], user_data['password'])
        
        # Create restaurant
        restaurant = Restaurant.objects.create(**validated_data)

        # Associate the user as the restaurant owner
        restaurant.created_by = user
        restaurant.save()

        return restaurant

# General Registration Serializer that will dynamically decide between company and restaurant registration
class RegistrationSerializer(serializers.Serializer):
    user_data = UserSerializer()
    company_data = CompanySerializer(required=False)
    restaurant_data = RestaurantSerializer(required=False)

    def validate(self, attrs):
        # Check if either company_data or restaurant_data is provided
        if 'company_data' in attrs and 'restaurant_data' in attrs:
            raise serializers.ValidationError(_("Please provide either company data or restaurant data, not both."))
        return attrs

    def create(self, validated_data):
        if 'company_data' in validated_data:
            company_data = validated_data.pop('company_data')
            company = CompanySerializer().create(company_data)
            return company

        elif 'restaurant_data' in validated_data:
            restaurant_data = validated_data.pop('restaurant_data')
            restaurant = RestaurantSerializer().create(restaurant_data)
            return restaurant

        else:
            raise serializers.ValidationError(_("Either company or restaurant data must be provided."))