from rest_framework import serializers
from allauth.account.adapter import get_adapter
from allauth.account.utils import setup_user_email
from allauth.account.utils import send_email_confirmation
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
from .models import CustomUser, Company, Restaurant, City, Country, RegionOrState, Branch, Menu, MenuCategory, MenuItem
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import Group

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
        fields = '__all__'

    def create(self, validated_data):
        # Assign role and use custom manager method to create the user
        role = self.context.get('role')
        if not role:
            raise serializers.ValidationError(_("A role must be specified in the context to create a user."))
        validated_data['role'] = role
        
        # Use the create_user_with_role method to create the user with the role
        user = CustomUser.objects.create_user_with_role(**validated_data)
        send_email_confirmation(self.context.get('request'), user)
        
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
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Company
        fields = ['name', 'about', 'contact_email', 'contact_phone', 'created_by']

    def create(self, validated_data):
        return Company.objects.create(**validated_data)

# Restaurant registration serializer
class RestaurantSerializer(serializers.ModelSerializer):
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    # company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all())

    class Meta:
        model = Restaurant
        fields = ['name', 'address', 'city', 'country', 'company', 'created_by']

    def create(self, validated_data):
        return Restaurant.objects.create(**validated_data)

# General Registration Serializer that will dynamically decide between company and restaurant registration
class RegistrationSerializer(serializers.Serializer):
    user_data = UserSerializer()
    company_data = CompanySerializer(required=False)
    restaurant_data = RestaurantSerializer(required=False)

    def validate(self, attrs):
        # Check if either company_data or restaurant_data is provided
        if 'user_data' not in attrs :
            raise serializers.ValidationError(_("Please provide user information"))
        if 'company_data' in attrs and 'restaurant_data' not in attrs :
            raise serializers.ValidationError(_("Main Restaurant branch must be added for Brands"))
        if 'company_data' not in attrs and 'restaurant_data' not in attrs:
            raise serializers.ValidationError(_("You must provide either 'company data' or 'restaurant data' data."))
        return attrs

    def create(self, validated_data):
        # Create the user first
        company = None
        role = self.context.get('role', 'user')
        user_data = validated_data.pop('user_data')
        user = CustomUser.objects.create_user_with_role(
            role=role,
            email=user_data['email'],
            phone_number=user_data['phone_number'],
            password=user_data['password'],
            first_name=user_data.get('first_name'),
            last_name=user_data.get('last_name')
        )
        # Trigger email confirmation
        send_email_confirmation(self.context.get('request'), user)

        company = None
        restaurant = None

        if 'company_data' in validated_data:
            company_data = validated_data.pop('company_data')
            company_data['created_by'] = user
            company = CompanySerializer().create(company_data)

            user.company = company
            user.save()

            # Add the user to the CompanyAdmin group
            company_admin_group, created = Group.objects.get_or_create(name='CompanyAdmin')
            user.groups.add(company_admin_group)

            # Create the restaurant if provided
            if 'restaurant_data' in validated_data:
                restaurant_data = validated_data.pop('restaurant_data')
                restaurant_data['created_by'] = user
                if company:
                    restaurant_data['company'] = company    
                restaurant = RestaurantSerializer().create(restaurant_data)
                user.restaurants.add(restaurant)

        elif 'restaurant_data' in validated_data:
            restaurant_data = validated_data.pop('restaurant_data')
            restaurant_data['created_by'] = user
            restaurant = Restaurant.objects.create(**restaurant_data)

            user.restaurants.add(restaurant)

            # Assign the user to the RestaurantOwner group
            restaurant_owner_group, created = Group.objects.get_or_create(name='RestaurantOwner')
            user.groups.add(restaurant_owner_group)

        else:
            raise serializers.ValidationError(_("Either company or restaurant data must be provided."))

        return {'user': user, 'company': company, 'restaurant': restaurant}

class BranchSerializer(serializers.ModelSerializer):
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Branch
        fields = ['id', 'restaurant', 'company', 'name', 'address', 'city', 'country', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user.groups.filter(name="RestaurantOwner").exists():
            validated_data['status'] = 'active'
        return super().create(validated_data)

class MenuCategorySerializer(serializers.ModelSerializer):
    items = MenuItemSerializer(many=True, read_only=True)

    class Meta:
        model = MenuCategory
        fields = ['id', 'name', 'items']

class MenuItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MenuItem
        fields = ['id', 'name', 'description', 'price']

class MenuSerializer(serializers.ModelSerializer):
    categories = MenuCategorySerializer(many=True, read_only=True)

    class Meta:
        model = Menu
        fields = ['id', 'name', 'categories']

class BranchMenuSerializer(serializers.ModelSerializer):
    menus = MenuSerializer(many=True, read_only=True)

    class Meta:
        model = Branch
        fields = ['id', 'name', 'menus']

