from rest_framework import serializers
from adrf.serializers import Serializer
from adrf.serializers import ModelSerializer
from allauth.account.adapter import get_adapter
from allauth.account.utils import setup_user_email
from asgiref.sync import sync_to_async 
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import Group
from .models import CustomUser, Company, Restaurant, City, Country, RegionOrState, Branch, Menu, MenuCategory, MenuItem, StaffShift, OvertimeRequest, StaffAvailability
from zMisc.utils import log_activity
import logging

logger = logging.getLogger(__name__)

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

class UserSerializer(ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    role = serializers.CharField(read_only=True)
    countries = serializers.PrimaryKeyRelatedField(queryset=Country.objects.all(), many=True, required=False)

    class Meta:
        model = CustomUser
        fields = '__all__'

    async def validate(self, attrs):
        """
        Asynchronously validate city-state relationship.
        """
        city = attrs.get('city')
        state = attrs.get('state')
        if city and state:
            # Use sync_to_async for potential DB field access
            city_region = await sync_to_async(lambda: city.region_or_state)()
            if city_region != state:
                city_name = await sync_to_async(lambda: city.name)()
                state_name = await sync_to_async(lambda: state.name)()
                raise serializers.ValidationError({
                    'city': _("The city '%(city_name)s' does not belong to the state/region '%(state_name)s'.") % {
                        'city_name': city_name,
                        'state_name': state_name
                    }
                })
        return attrs

    async def create(self, validated_data):
        """
        Fully async user creation with proper coroutine handling
        """
        # Ensure we have a proper dictionary (not coroutine)
        if hasattr(validated_data, '__await__'):
            validated_data = await validated_data
        
        role = self.context.get('role') or validated_data.pop('role', None)
        if not role:
            raise serializers.ValidationError("A role must be specified")

        # Handle M2M fields safely
        m2m_fields = {}
        for field in ['companies', 'countries', 'restaurants', 'branches']:
            if field in validated_data:
                m2m_fields[field] = validated_data.pop(field)
        
        # Create user
        user = await sync_to_async(CustomUser.objects.create_user_with_role)(
            **validated_data,
            role=role
        )

        # Process M2M relationships
        for field, values in m2m_fields.items():
            if values:
                await sync_to_async(getattr(user, field).set)(values)
                role_value = await sync_to_async(user.get_role_value)()
                if role_value < 5:
                    user.status = 'active'
                    await sync_to_async(user.save)(update_fields=['status'])

        # Email handling
        self.context["email_sent"] = False
        try:
            from .tasks import send_register_email
            await sync_to_async(send_register_email.delay)(user.id)
            self.context["email_sent"] = True
        except Exception as e:
            logger.error(f"Email failed for user {user.id}: {str(e)}")

        # Track History: Log activity with constructed details
        details = {}
        details['username'] = getattr(user, 'username', None)
        details['role'] = user.get_role_display()
        await log_activity(user, 'staff_hire', details)

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
class CompanySerializer(ModelSerializer):
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Company
        fields = ['name', 'about', 'contact_email', 'contact_phone', 'created_by']

    async def create(self, validated_data):
        try:
            company = await Company.objects.acreate(**validated_data)

            return company
        except Exception as e:
            raise serializers.ValidationError(str(e))

# Restaurant registration serializer
class RestaurantSerializer(ModelSerializer):
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())
    # company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all())

    class Meta:
        model = Restaurant
        fields = ['name', 'address', 'city', 'country', 'company', 'status', 'created_by']

    async def create(self, validated_data):
        validated_data['status'] = 'active'
        restaurant = await Restaurant.objects.acreate(**validated_data)

        return restaurant

# General Registration Serializer that will dynamically decide between company and restaurant registration
class RegistrationSerializer(Serializer):
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

    async def create(self, validated_data):
        # Create the user using UserSerializer
        user_data = validated_data.pop('user_data')  # Contains objects already
        user = await UserSerializer(context=self.context).create(validated_data=user_data)

        # Get email_sent from context (set by UserSerializer)
        email_sent = self.context.get("email_sent", False)

        company = None
        restaurant = None

        if 'company_data' in validated_data:
            company_data = validated_data.pop('company_data')
            company_data['created_by'] = user
            company = await CompanySerializer().create(company_data)

            await sync_to_async(user.companies.add)(company)

            # Add the user to the CompanyAdmin group
            company_admin_group, created = await sync_to_async(Group.objects.get_or_create)(name='CompanyAdmin')
            await sync_to_async(user.groups.add)(company_admin_group)

            # Create the restaurant if provided
            details = {}
            if 'restaurant_data' in validated_data:
                restaurant_data = validated_data.pop('restaurant_data')
                restaurant_data['created_by'] = user
                if company:
                    restaurant_data['company'] = company   
                    details['company'] = company.id
                restaurant = await RestaurantSerializer().create(restaurant_data)
                await sync_to_async(user.restaurants.add)(restaurant)

                details['name'] = getattr(restaurant, 'name', None)
                
                await log_activity(user, 'restaurant_create', details, restaurant)

        elif 'restaurant_data' in validated_data:
            restaurant_data = validated_data.pop('restaurant_data')
            restaurant_data['created_by'] = user
            restaurant = await RestaurantSerializer().create(restaurant_data)

            await sync_to_async(user.restaurants.add)(restaurant)

            # Assign the user to the RestaurantOwner group
            restaurant_owner_group, created = await sync_to_async(Group.objects.get_or_create)(name='RestaurantOwner')
            await sync_to_async(user.groups.add)(restaurant_owner_group)

            details = {}
            details['name'] = getattr(restaurant, 'name', None)
            await log_activity(user, 'restaurant_create', details, restaurant)

        else:
            raise serializers.ValidationError(_("Either company or restaurant data must be provided."))

        return { 'user': user, 'email_sent': email_sent }

    async def to_representation(self, instance):
        if hasattr(instance, '__await__'):  # Another way to check for coroutines
            instance = await instance
        
        return {
            'message': _("Registration successful!"),
            'email_sent': instance['email_sent'],
            'user': {
                "username": instance['user'].username,
                "user_id": instance['user'].email,
            }
        }

class BranchSerializer(ModelSerializer):
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Branch
        fields = ['id', 'restaurant', 'company', 'name', 'address', 'city', 'country', 'timezone', 'manager', 'created_by']

    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user.groups.filter(name="RestaurantOwner").exists():
            validated_data['status'] = 'active'
        return super().create(validated_data)


class MenuItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MenuItem
        fields = ['id', 'name', 'description', 'price']


class MenuCategorySerializer(serializers.ModelSerializer):
    items = MenuItemSerializer(many=True, read_only=True)

    class Meta:
        model = MenuCategory
        fields = ['id', 'name', 'items']


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

class StaffShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffShift
        fields = '__all__'

class OvertimeRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = OvertimeRequest
        fields = '__all__'
        read_only_fields = ('staff_shift', 'requested_at', 'manager_response_at')

class StaffAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffAvailability
        fields = '__all__'


class AssignmentSerializer(serializers.Serializer):
    object_type = serializers.ChoiceField(choices=['user', 'branch', 'restaurant'])
    object_id = serializers.IntegerField()
    field_name = serializers.CharField()
    user_id = serializers.IntegerField(required=False)
    field_value = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    force_update = serializers.CharField(required=False)
    action = serializers.CharField(required=False)

    def validate(self, data):
        if 'user_id' in data and 'field_value' in data:
            raise serializers.ValidationError(_("Specify either user_id for assignment or field_value for update, not both"))
        if 'user_id' not in data and 'field_value' not in data:
            raise serializers.ValidationError(_("Specify either user_id or field_value"))
        return data