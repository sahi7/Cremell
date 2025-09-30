import asyncio

from rest_framework import serializers
from adrf.serializers import Serializer, ModelSerializer
from allauth.account.adapter import get_adapter
from allauth.account.utils import setup_user_email
from asgiref.sync import sync_to_async, async_to_sync
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.password_validation import validate_password
from django.db import connection

from .models import *
from .tasks import log_activity
from zMisc.atransactions import aatomic

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
import time
class UserSerializer(ModelSerializer):
    start = time.perf_counter()
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    role = serializers.CharField(read_only=True)
    print(f"user serializer took {(time.perf_counter() - start) * 1000:.3f} ms")
    # countries = serializers.PrimaryKeyRelatedField(queryset=Country.objects.all(), many=True, required=False)

    class Meta:
        model = CustomUser
        fields = '__all__'

    # async def validate(self, attrs):
    #     """
    #     Asynchronously validate city-state relationship.
    #     """
    #     city = attrs.get('city')
    #     state = attrs.get('state')
    #     if city and state:
    #         # Use sync_to_async for potential DB field access
    #         # city_region = await sync_to_async(lambda: city.region_or_state)()
    #         city_region = await sync_to_async(city.region_or_state_id)()
    #         if city_region != state:
    #             # city_name = await sync_to_async(lambda: city.name)()
    #             # state_name = await sync_to_async(lambda: state.name)()
    #             # raise serializers.ValidationError({
    #             #     'city': _("The city '%(city_name)s' does not belong to the state/region '%(state_name)s'.") % {
    #             #         'city_name': city_name,
    #             #         'state_name': state_name
    #             #     }
    #             # })
    #             raise serializers.ValidationError({
    #                 'detail': _("The city does not belong to the state/region")
    #             })
    #     return attrs

    async def create(self, validated_data):
        """
        Fully async user creation with proper coroutine handling
        """
        start = time.perf_counter()
        from cre.tasks import notify_and_log
        # Ensure we have a proper dictionary (not coroutine)
        if hasattr(validated_data, '__await__'):
            validated_data = await validated_data
        
        role = self.context.get('role') or validated_data.pop('role', None)
        status = self.context.get('status') or validated_data.pop('status', None)
        wind_direction = self.context.get('wind_direction')
        validated_data['status'] = status
        if not role:
            raise serializers.ValidationError(_("A role must be specified"))
        print(f"1st section took {(time.perf_counter() - start) * 1000:.3f} ms")
        
        # Remove M2M fields from validated_data to avoid direct mapping errors
        start = time.perf_counter()
        m2m_fields = {}
        val_fields =['countries']
        if not wind_direction:
            val_fields.extend(['companies', 'restaurants', 'branches'])
        
        for field in val_fields:
            if field in validated_data and validated_data[field]:
                values = validated_data.pop(field)
                m2m_fields[field] = [v.id if hasattr(v, 'id') else v for v in values] if values else []
        print(f"2nd section took {(time.perf_counter() - start) * 1000:.3f} ms")

        # Create user
        start = time.perf_counter()
        user = await CustomUser.objects.create_user_with_role(**validated_data, role=role)
        print(f"3rd user creation took {(time.perf_counter() - start) * 1000:.3f} ms")

        # Set M2M relationships concurrently
        start = time.perf_counter()
        # for field, values in m2m_fields.items():
        #     if values:
        #         await getattr(user, field).aset(values)

        m2m_objects = []
        field_to_id = {
            'companies': 'company_id',
            'countries': 'country_id',
            'restaurants': 'restaurant_id',
            'branches': 'branch_id',
            'groups': 'group_id'
        }
        for field, values in m2m_fields.items():
            if values:
                m2m_manager = getattr(CustomUser, field).through
                object_id_field = field_to_id[field]
                m2m_objects = [
                    m2m_manager(customuser_id=user.id, **{object_id_field: value})
                    for value in values
                ]
                await m2m_manager.objects.abulk_create(m2m_objects, ignore_conflicts=True)
        print(f"M2M gathering took {(time.perf_counter() - start) * 1000:.3f} ms")

        # Preparing Log
        start = time.perf_counter()
        # notify_and_log.delay(user_id=user.id, m2m_field_ids=m2m_fields)
        self.context["email_sent"] = True
        print(f"Notification task took {(time.perf_counter() - start) * 1000:.3f} ms")

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

    class Meta:
        model = Company
        fields = ['id', 'name', 'about', 'contact_email', 'contact_phone']

    async def create(self, validated_data):
        try:
            company = await Company.objects.acreate(**validated_data)

            return company
        except Exception as e:
            raise serializers.ValidationError(str(e))

# Restaurant registration serializer
class RestaurantSerializer(ModelSerializer):

    class Meta:
        model = Restaurant
        fields = ['id', 'name', 'address', 'city', 'country', 'company', 'status', 'manager']

    async def create(self, validated_data):
        validated_data['status'] = 'active'
        company = validated_data.get('company') 
        restaurant = await Restaurant.objects.acreate(**validated_data)

        details = {}
        details['name'] = getattr(restaurant, 'name', None)
        if company:
            details['company'] = company.id
   
        log_activity.delay(validated_data['created_by_id'] , 'restaurant_create', details, restaurant.id)

        return restaurant
    
class BranchSerializer(ModelSerializer):
    # restaurant = serializers.IntegerField(source='restaurant_id', required=False)
    # company = serializers.IntegerField(source='company_id', required=False)
    # country = serializers.IntegerField(source='country_id', required=True)

    class Meta:
        model = Branch
        fields = ['id', 'restaurant', 'company', 'name', 'address', 'city', 'country', 'status', 'timezone', 'default_language', 'manager', 'logo']
        read_only_fields = ('created_by', )

    def validate(self, attrs):
        # print("attrs: ", attrs)
        return attrs

    async def create(self, validated_data):
        from notifications.tasks import invalidate_cache_keys
        request = self.context.get('request')
        is_RO = self.context.get('is_CEO', False)
        if is_RO:
            validated_data['status'] = 'active'
        user_id = (request.user.id if request and request.user else None) or validated_data['created_by_id']
        validated_data['created_by_id'] = user_id
        branch = await Branch.objects.acreate(**validated_data)
        details = {}
        details['name'] = branch.name
        details['restaurant'] = branch.restaurant_id
        log_activity.delay(validated_data['created_by_id'], 'branch_create', details, branch.id, 'branch')

        # Build cache keys
        user_role = (request.user.role if request and request.user else None) or validated_data.get('role', 'default')
        cache_keys = [f'user_scopes:{user_id}', f'scopes:{user_role}:{user_id}']
        invalidate_cache_keys.delay(cache_keys, [user_id])
        
        return branch
    
import time
# General Registration Serializer that will dynamically decide between company and restaurant registration
class  RegistrationSerializer(Serializer):
    from subscriptions.serializers import SubscriptionSerializer

    # user_data = UserSerializer()
    company_data = CompanySerializer(required=False)
    restaurant_data = RestaurantSerializer(required=False)
    branch_data = BranchSerializer(required=False)
    subscription_data = SubscriptionSerializer()

    @aatomic()
    def create_transaction(self, validated_data, context):
        start = time.perf_counter()
        # 1. Create User
        print("validated_data: ", validated_data)
        # user_data = validated_data.pop('user_data')
        # user_serializer = UserSerializer(context=context)
        # user = async_to_sync(user_serializer.create)(user_data)  # Raw SQL create
        user = {
            'id': 66,
            'role': "user_role",
            'username': "username",
            'email': "email@gmail.com"
        }
        print(f"1 user creation took {(time.perf_counter() - start) * 1000:.3f} ms")

        # Get email_sent from context
        email_sent = context.get("email_sent", False)

        company = None
        restaurant = None

        # 2. Create Company if provided
        if 'company_data' in validated_data:
            start = time.perf_counter()
            company_data = validated_data.pop('company_data')
            company_data['created_by_id'] = user.id
            company = async_to_sync(CompanySerializer().create)(company_data)

            # Add M2M relationship
            entity_id = company.id
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO cre_customuser_companies (customuser_id, company_id)
                    VALUES (%s, %s)
                    """,
                    [user.id, entity_id]
                )

            # Add to CompanyAdmin group (assume sync add_to_group)
            async_to_sync(user.add_to_group)(user.role)  # If async, wrap in sync_to_async
            print(f"2 company creation took {(time.perf_counter() - start) * 1000:.3f} ms")

            # 3. Create Restaurant if provided
            if 'restaurant_data' in validated_data:
                start = time.perf_counter()
                restaurant_data = validated_data.pop('restaurant_data')
                restaurant_data['created_by_id'] = user.id
                restaurant_data['company_id'] = entity_id
                restaurant = async_to_sync(RestaurantSerializer().create)(restaurant_data)

                # Add M2M relationship
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO cre_customuser_restaurants (customuser_id, restaurant_id)
                        VALUES (%s, %s)
                        """,
                        [user.id, restaurant.id]
                    )
                print(f"3 restaurant creation took {(time.perf_counter() - start) * 1000:.3f} ms")

        # 4. Create Restaurant without Company
        elif 'restaurant_data' in validated_data:
            start = time.perf_counter()
            restaurant_data = validated_data.pop('restaurant_data')
            restaurant_data['created_by_id'] = user.id
            restaurant = async_to_sync(RestaurantSerializer().create)(restaurant_data)

            # Add M2M relationship
            entity_id = restaurant.id
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO cre_customuser_restaurants (customuser_id, restaurant_id)
                    VALUES (%s, %s)
                    """,
                    [user.id, entity_id]
                )

            # Add to RestaurantOwner group
            async_to_sync(user.add_to_group)(user.role)  # If async, wrap in sync_to_async
            print(f"4 restaurant creation took {(time.perf_counter() - start) * 1000:.3f} ms")

        # 4. Create Restaurant without Company
        elif 'branch_data' in validated_data:
            start = time.perf_counter()
            branch_data = validated_data.pop('branch_data')
            # branch_data['created_by_id'] = user.id
            branch_data['created_by_id'] = user['id']
            branch = async_to_sync(BranchSerializer().create)(branch_data)

            # Add M2M relationship
            entity_id = branch.id
            # with connection.cursor() as cursor:
            #     cursor.execute(
            #         """
            #         INSERT INTO cre_customuser_branches (customuser_id, branch_id)
            #         VALUES (%s, %s)
            #         """,
            #         [user.id, entity_id]
            #     )
            # Add to BranchManager group
            # async_to_sync(user.add_to_group)(user.role)  # If async, wrap in sync_to_async
            print(f"5 branch creation took {(time.perf_counter() - start) * 1000:.3f} ms")
        else:
            raise serializers.ValidationError(_("Unable to detect user scope."))
        
        subscription_data = validated_data.pop('subscription_data')
        # Generate unique IDs
        request_id = str(uuid.uuid4())
        subscription_id = str(uuid.uuid4())
        entity_type = context.get("entity_type", None)
        
        # from redis import Redis
        redis_conn = settings.SYNC_REDIS
        redis_conn.xadd('rms.stream', {
            'type': 'subscrition.create',
            'subscription_id': subscription_id,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'plan_id': str(subscription_data['plan_id']),
            'feature_ids': json.dumps(subscription_data.get('feature_ids')),
        })

        return {'user': user, 'email_sent': email_sent, 'subscription_id': subscription_id,}

    async def create(self, validated_data):
        start = time.perf_counter()
        try:
            # Run transactional operations
            result = await self.create_transaction(validated_data, self.context)
            print(f"Total create took {(time.perf_counter() - start) * 1000:.3f} ms")
            return result
        except Exception as e:
            logger.error(f"Creation failed: {str(e)}", exc_info=True)
            raise serializers.ValidationError(f"Failed to create resources: {str(e)}")

    async def to_representation(self, instance):
        if hasattr(instance, '__await__'):
            instance = await instance
        
        return {
            'message': "Registration successful!",
            'email_sent': instance['email_sent'],
            'subscription_id': instance['subscription_id'],
            # 'user': {
            #     'username': instance['user'].username,
            #     'email': instance['user'].email,  # Fixed user_id to email
            # }
        }

class MenuSerializer(ModelSerializer):
    class Meta:
        model = Menu
        fields = ['id', 'name', 'description', 'branch', 'is_pos_menu', 'is_web_or_app_menu', 'created_by']
        read_only_fields = ['created_by']

    def validate(self, data):
        """Validate that branch_id is in the user's branches from request.data['branches']."""
        branch = data.get('branch')
        name = data.get('name')
        if not name or not branch:
            raise serializers.ValidationError(_("The Menu name & Branch[] is required."))
        return data

    async def create(self, validated_data):
        """
        Async create method to save Menu instance using asave.
        """
        request = self.context.get('request')
        validated_data['created_by'] = request.user
        instance = Menu(**validated_data)
        await instance.asave()
        return instance

class MenuCategorySerializer(ModelSerializer):
    created_by = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = MenuCategory
        fields = ['id', 'name', 'description', 'menu', 'created_by']

    async def create(self, validated_data):
        """
        Async create method to save MenuCategory instance using asave.
        """
        instance = MenuCategory(**validated_data)
        await instance.asave()
        return instance
    
class MenuItemSerializer(ModelSerializer):
    categories = MenuCategorySerializer(many=True, read_only=True)
    class Meta:
        model = MenuItem
        fields = ['id', 'name', 'description', 'price', 'is_available', 'category', 'categories']

    def validate(self, data):
        """Validate that category and name."""
        category = data.get('category')
        if not data.get('name') or not category:
            raise serializers.ValidationError(_("The Item's name & category is required."))
        
        return data

    async def create(self, validated_data):
        """
        Async create method to save MenuItem instance using asave.
        """
        request = self.context.get('request')
        validated_data['created_by'] = request.user
        instance = MenuItem(**validated_data)
        await instance.asave()
        return instance

class BranchMenuSerializer(ModelSerializer):
    # Extend this to include the menu category and MenuItems
    # redis save as pos_menu
    menus = serializers.SerializerMethodField() # select only those with is_pos_menu=True

    class Meta:
        model = Branch
        fields = ['id', 'name', 'menus']
    
class OrderItemSerializer(ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ['id', 'menu_item', 'quantity', 'item_price']
        extra_kwargs = {
            'item_price': {'read_only': True}  # Never accept input
        }

class OrderSerializer(ModelSerializer):
    items = OrderItemSerializer(many=True, write_only=False)
    
    class Meta:
        model = Order
        fields = ['id', 'status', 'branch', 'order_type', 'source', 'table_number', 'special_instructions', 'version', 'total_price', 'items']
        read_only_fields = ('created_by', 'deleted_by', 'is_active', 'total_price')

    def validate(self, data):
        """Custom validation for order_type and table_number."""
        order_type = data.get('order_type')
        table_number = data.get('table_number')
        if order_type == 'dine_in' and not table_number:
            raise serializers.ValidationError(_("Table number is required for dine-in orders."))
        if order_type and (order_type != 'dine_in' and table_number):
            raise serializers.ValidationError(_("Table number is only applicable for dine-in orders."))
        if not data.get('items'):
            raise serializers.ValidationError(_("At least one order item is required."))
        # print("data: ", data)
        return data

class ShiftSerializer(ModelSerializer):
    """Serializer for Shift model with async validation."""

    class Meta:
        model = Shift
        fields = ['id', 'branch', 'name', 'start_time', 'end_time']
        read_only_fields = ['id']

    def validate(self, attrs):
        """Validate shift times and prevent overlaps."""
        start_time = attrs.get('start_time')
        end_time = attrs.get('end_time')

        if start_time >= end_time:
            raise serializers.ValidationError({
                'start_time': 'Start time must be before end time.'
            })

        return attrs
    
class ShiftSwapRequestSerializer(serializers.ModelSerializer):

    class Meta:
        model = ShiftSwapRequest
        fields = [
            'id', 'branch', 'initiator', 'initiator_shift', 'desired_date',
            'counterparty', 'counterparty_shift', 'status', 'accepted_at'
        ]
        read_only_fields = ['initiator', 'counterparty', 'status', 'created_at', 'updated_at', 'accepted_at']
    
    def validate(self, attrs):
        from datetime import date
        initiator_shift = attrs.get('initiator_shift')
        desired_date = attrs.get('desired_date')
        counterparty_shift = attrs.get('counterparty_shift', None)

        if not initiator_shift or not desired_date:
            raise serializers.ValidationError(_("Initiator & date are required."))
        if desired_date and desired_date < date.today():
            raise serializers.ValidationError("Desired date cannot be in the past.")

        if counterparty_shift:
            if self.instance and self.instance.status != 'pending':
                raise serializers.ValidationError("Swap request is not pending.")
            if initiator_shift.role != counterparty_shift.role:
                raise serializers.ValidationError("Shifts must be for the same role.")
            if counterparty_shift.start_time.date() != desired_date:
                raise serializers.ValidationError("Counterparty shift must match the desired date.")

        return attrs
    
class ShiftPatternConfigSerializer(serializers.Serializer):
    """Dynamic serializer for pattern config validation"""
    def validate(self, data):
        pattern_type = self.context.get('pattern_type')
        data = self.context.get('data')
        branch_id = self.context.get('branch_id')
        shift_ids = set()
        out_of_scope = {'invalid_shifts': []}
        
        if pattern_type == ShiftPattern.PatternType.ROLE_BASED:
            if not isinstance(data.get('default_shift'), int):
                raise serializers.ValidationError("Role-based requires numeric default_shift")
            if 'exceptions' in data:
                if not isinstance(data['exceptions'].get('days'), list):
                    raise serializers.ValidationError("Exceptions days must be a list")
                if not isinstance(data['exceptions'].get('shift'), int):
                    raise serializers.ValidationError("Exceptions shift must be numeric")
            
            # Shift ids check 
            if data.get('exceptions', {}).get('shift'):
                shift_ids.add(data['exceptions']['shift'])
            if data.get('default_shift'):
                shift_ids.add(data['default_shift'])
        
        elif pattern_type == ShiftPattern.PatternType.USER_SPECIFIC:
            if not isinstance(data.get('fixed_schedule'), list):
                raise serializers.ValidationError("User-specific requires fixed_schedule list")
            for entry in data['fixed_schedule']:
                if not isinstance(entry.get('day'), str):
                    raise serializers.ValidationError("Schedule entries require day string")

            # Shift ids check     
            for schedule in data.get('fixed_schedule', []):
                if schedule.get('shift') and schedule['shift'] != 'OFF':
                    shift_ids.add(schedule['shift'])
        
        elif pattern_type == ShiftPattern.PatternType.ROTATING:
            if not isinstance(data.get('cycle_length'), int) or data['cycle_length'] <= 0:
                raise serializers.ValidationError("Rotating requires positive cycle_length")
            if not isinstance(data.get('pattern'), list):
                raise serializers.ValidationError("Rotating requires pattern list")
            for week in data['pattern']:
                if not isinstance(week.get('shifts'), list):
                    raise serializers.ValidationError("Week entries require shifts list")
                
            # Shift ids check 
            for pattern in data.get('pattern', []):
                shift_ids.update(s for s in pattern.get('shifts', []) if s)
        
        elif pattern_type == ShiftPattern.PatternType.HYBRID:
            if not isinstance(data.get('components'), list):
                raise serializers.ValidationError("Hybrid requires components list")
            for component in data['components']:
                if not component.get('type') in ['ROLE_BASED', 'USER_SPECIFIC', 'ROTATING', 'AD_HOC']:
                    raise serializers.ValidationError("Invalid component type")
                
            # Shift ids check 
            for component in data.get('components', []):
                if component.get('shift'):
                    shift_ids.add(component['shift'])
                if component.get('type') == 'ROTATING':
                    for pattern in component.get('pattern', []):
                        shift_ids.update(s for s in pattern.get('shifts', []) if s)


        elif pattern_type == ShiftPattern.PatternType.AD_HOC:
            # Shift ids check 
            for component in data.get('components', []):
                if component.get('shift'):
                    shift_ids.add(component['shift'])
                if component.get('type') == 'ROTATING':
                    for pattern in component.get('pattern', []):
                        shift_ids.update(s for s in pattern.get('shifts', []) if s)
        from redis import Redis
        if shift_ids:
            print("shift_ids: ", shift_ids)
            cache_key = f"shift_ids:branch_{branch_id}"
            cache = Redis.from_url(settings.REDIS_URL, decode_responses=True)
            valid_shift_ids = cache.get(cache_key)
            if valid_shift_ids is None:
                valid_shift_ids = set(
                    Shift.objects.filter(branch_id=branch_id)
                    .values_list('id', flat=True)
                    .iterator()
                )
                cache.set(cache_key, json.dumps(list(valid_shift_ids)), ex=3600)
            else:
                # Parse the JSON string back into a Python set
                valid_shift_ids = set(json.loads(valid_shift_ids))
                
            out_of_scope['invalid_shifts'].extend(list(shift_ids - valid_shift_ids))
            
        if out_of_scope['invalid_shifts']:
            raise serializers.ValidationError(_("Invalid shifts: %(shifts)s") % {'shifts': out_of_scope['invalid_shifts']})
        
        return data

class ShiftPatternSerializer(serializers.ModelSerializer):
    config = serializers.JSONField(binary=False)
    
    class Meta:
        model = ShiftPattern
        fields = '__all__'
        extra_kwargs = {
            'priority': {'min_value': 1, 'max_value': 1000},
            'active_from': {'required': True},
            'users': {'required': False}
        }
    
    def validate(self, data):
        # raw_config = data.get('config')
        # print("Raw config:", raw_config, type(raw_config)) 
        # Validate at least one target exists
        if not data.get('users') and not data.get('roles'):
            raise serializers.ValidationError(_("Must specify either user or role"))
        if data["active_from"] < timezone.now().date():
            raise serializers.ValidationError({ "active_from": _("Cannot set date in the past") })
        if data.get("active_until") is not None:
            if data["active_from"] > (data.get("active_until")):
                raise serializers.ValidationError(_("Active from date must be before active until date."))
        if data.get("pattern_type") != "RT" and data.get("active_until") is None:
            raise serializers.ValidationError(_("Active until date is required for non-RT pattern types."))
            
        branch = data.get('branch')
        branch_id = branch.id if isinstance(branch, Branch) else branch

        # Validate config against pattern type
        config_serializer = ShiftPatternConfigSerializer(
            data=data.get('config', {}),
            context={
                'pattern_type': data.get('pattern_type'), 
                'data': data.get('config', {}),
                'branch_id': branch_id
                }
        )
        config_serializer.is_valid(raise_exception=True)
        
        return data

class StaffShiftSerializer(ModelSerializer):
    class Meta:
        model = StaffShift
        fields = '__all__'

class OvertimeRequestSerializer(ModelSerializer):
    class Meta:
        model = OvertimeRequest
        fields = '__all__'
        read_only_fields = ('is_approved', 'staff_shift', 'requested_at', 'manager_response_at')

    async def create(self, validated_data):
        try:
            ot_request = await OvertimeRequest.objects.acreate(**validated_data)
            logger.info(f"OvertimeRequest created with ID {ot_request.id}")
            return ot_request
        except Exception as e:
            logger.error(f"Error creating OvertimeRequest: {str(e)}")
            raise serializers.ValidationError(str(e))

class StaffAvailabilitySerializer(ModelSerializer):
    class Meta:
        model = StaffAvailability
        fields = '__all__'


class AssignmentSerializer(Serializer):
    object_type = serializers.ChoiceField(choices=['user', 'branch', 'restaurant', 'rule'])
    object_id = serializers.IntegerField()
    field_name = serializers.CharField()
    user_id = serializers.IntegerField(required=False)
    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=False  # Prevents empty lists
    )
    field_value = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    force_update = serializers.CharField(required=False)
    action = serializers.CharField(required=False)

    def validate(self, data):
        if 'user_id' in data and 'field_value' in data:
            raise serializers.ValidationError(_("Specify either user_id for assignment or field_value for update, not both"))
        if 'user_id' not in data and 'field_value' not in data and 'user_ids' not in data:
            raise serializers.ValidationError(_("Specify either user_id(s) or field_value"))
        return data