import uuid
import pytz
import json

from typing import List, Union, AsyncGenerator
from django.core.cache import cache
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.postgres.fields import ArrayField
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.conf import settings
from django.db import models
from rest_framework.exceptions import ValidationError
from redis.asyncio import Redis

class CustomUserManager(BaseUserManager):
    async def create_user(self, email=None, username=None, phone_number=None, password=None, **extra_fields):
        if not email and not username and not phone_number:
            raise ValueError(_('The Email, Username, or Phone number field must be set'))
        
        email = self.normalize_email(email) if email else None
        user = self.model(email=email, username=username, phone_number=phone_number, **extra_fields)
        user.set_password(password)
        await user.asave(using=self._db)
        return user

    async def create_superuser(self, email=None, username=None, phone_number=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return await self.create_user(email, username, phone_number, password, **extra_fields)

    async def create_user_with_role(self, role, email, phone_number, password=None, **extra_fields):
        if not email and not phone_number:
            raise ValueError(_('The Email or Phone number field must be set'))
        if not role:
            raise ValueError(_('A role must be specified when creating a user'))
        extra_fields.setdefault('role', role)
        email = self.normalize_email(email) if email else None
        if role in ['company_admin', 'restaurant_owner']:
            extra_fields.setdefault('is_staff', True)
        return await self.create_user(email=email, phone_number=phone_number, password=password, **extra_fields)

    # Role-specific convenience method
    async def create_delivery_man(self, email=None, phone_number=None, password=None, **extra_fields):
        return await self.create_user_with_role('delivery_man', email=email, phone_number=phone_number, password=password, **extra_fields)
    

class CustomUser(AbstractUser):
    GENDER_CHOICES = (
        ('male', _('Male')),
        ('female', _('Female')),
        ('other', _('Other')),
    )
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    companies = models.ManyToManyField('Company', related_name="users", blank=True)
    countries = models.ManyToManyField('Country', related_name="users", blank=True)
    restaurants = models.ManyToManyField('Restaurant', related_name='employees', blank=True)
    branches = models.ManyToManyField('Branch', related_name='employees', blank=True)

    # Contact Information
    email = models.EmailField(unique=True, blank=True, null=True)
    phone_number = models.CharField(max_length=20, unique=True, blank=True, null=True)
    address_line_1 = models.CharField(max_length=255, blank=True, null=True)
    address_line_2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.ForeignKey('City', on_delete=models.SET_NULL, blank=True, null=True, related_name="employees")
    state = models.ForeignKey('RegionOrState', on_delete=models.SET_NULL, blank=True, null=True, related_name="employees")
    postal_code = models.CharField(max_length=20, blank=True, null=True)

    # Role-specific details
    ROLE_CHOICES = (
        ('company_admin', _('Company Admin')),
        ('restaurant_owner', _('Restaurant Owner')),
        ('country_manager', _('Country Manager')),
        ('restaurant_manager', _('Restaurant Manager')),
        ('branch_manager', _('Branch Manager')),
        ('shift_leader', _('Shift Leader')),
        ('cashier', _('Cashier')),
        ('cook', _('Cook')),
        ('food_runner', _('Food Runner')),
        ('cleaner', _('Cleaner')),
        ('delivery_man', _('Delivery Man')),
        ('utility_worker', _('Utility Worker')),
    )
    STATUS_CHOICES = (
        ('inactive', _('Inactive')),
        ('pending', _('Pending schedule')),
        ('active', _('Active')),
        ('suspended', _('Suspended')),
        ('on_leave', _('On Leave')),
    )
    preferred_language = models.CharField(max_length=10, choices=settings.LANGUAGES, default='en',
                                help_text=_('User’s preferred language. Falls back to branch or company language.'))
    timezone = models.CharField(max_length=50, choices=[(tz, tz) for tz in pytz.common_timezones], default='UTC',
                                help_text=_('User’s preferred timezone. Falls back to branch timezone.'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', blank=True, null=True)
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, blank=True, null=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    hire_date = models.DateField(blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    r_val = models.PositiveIntegerField(default=12)

    objects = CustomUserManager()

    async def get_role_value(self, role: str = None):
        """
        Map the role string to a numeric value for comparison.
        """
        role_hierarchy = {
            'company_admin': 1,  # Top within company scope
            'restaurant_owner': 1,   # Top within restaurant scope
            'country_manager': 3,
            'restaurant_manager': 4,
            'branch_manager': 5,
            'shift_leader': 6,
            'cashier': 7,
            'cook': 8,
            'food_runner': 9,
            'cleaner': 10,
            'delivery_man': 11,
            'utility_worker': 12,
        }
        # return role_hierarchy.get(role or self.role, 0)  # If no role is specified, default to the instance's role
        # Use provided role or instance's role
        # print("DB HIT?", getattr(self, "_prefetched_objects_cache", False))
        target_role = role if role is not None else getattr(self, 'role', None) 
        cache_key = f"role_id:user_{self.id}:{target_role}"
        cache = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        cached = await cache.get(cache_key)
        if cached is not None:
            return int(cached)
        
        role_id = role_hierarchy.get(target_role, 0)

        # Cache role_id
        await cache.set(cache_key, str(role_id), ex=3600)
        return role_id

    async def manage_group(self, role, action='add'):
        """
        Add or remove user from the corresponding group based on their role.
        Actions: 'add' or 'remove'
        """
        group_map = {
            'company_admin': 'CompanyAdmin',
            'restaurant_owner': 'RestaurantOwner',
            'country_manager': 'CountryManager',
            'restaurant_manager': 'RestaurantManager',
            'branch_manager': 'BranchManager',
            'user': 'User',
        }
        group_name = group_map.get(role)
        if group_name:
            group = await Group.objects.aget(name=group_name)
            if action == 'add':
                await self.groups.aadd(group)
            elif action == 'remove':
                await self.groups.aremove(group)
            elif action == 'get':
                return group_name
    
    async def add_to_group(self, role):
        await self.manage_group(role)

    async def remove_from_group(self, role):
        await self.manage_group(role, action='remove')

    async def get_group(self, role):
        return await self.manage_group(role, action='get')

    async def get_timezone_language(user_ids):
        """
        Bulk fetch timezones AND languages for 1+ users with caching.
        Returns: {'user_id': {'timezone': str, 'language': str}, ...}
        """
        if not user_ids:
            return {}

        user_ids = [user_ids] if isinstance(user_ids, int) else list(user_ids)
        redis = Redis.from_url('redis://localhost:6379')
        
        # 1. Try Redis cache for both settings
        cache_keys = [f'user_settings_{uid}' for uid in user_ids]
        cached = await redis.mget(cache_keys)
        
        # 2. Parse cached data
        result = {}
        missing_ids = []
        
        for uid, data in zip(user_ids, cached):
            if data:
                try:
                    result[uid] = json.loads(data)
                except (json.JSONDecodeError, AttributeError):
                    missing_ids.append(uid)
            else:
                missing_ids.append(uid)
        
        # 3. Bulk fetch missing from DB
        if missing_ids:
            users = {u.id: u async for u in 
                    CustomUser.objects.filter(id__in=missing_ids)
                    .only('id', 'timezone', 'preferred_language')
                    .prefetch_related(
                        'restaurants',
                        'branches',
                        'companies',
                        'countries'
                    )}
            
            # 4. Process each missing user
            for uid in missing_ids:
                user = users.get(uid)
                if not user:
                    result[uid] = {'timezone': 'UTC', 'language': 'en'}
                    continue
                    
                # Get timezone
                tz = (user.timezone or 
                    await _get_first_attr(user.restaurants, 'timezone') or
                    await _get_first_attr(user.branches, 'timezone') or
                    await _get_first_attr(user.countries, 'timezone') or
                    'UTC')
                
                # Get language
                lang = (user.preferred_language or
                    await _get_first_attr(user.restaurants, 'default_language') or
                    await _get_first_attr(user.branches, 'default_language') or
                    await _get_first_attr(user.companies, 'default_language') or
                    await _get_first_attr(user.countries, 'default_language') or
                    'en')
                
                result[uid] = {'timezone': tz, 'language': lang}
                
                # Cache combined result
                await redis.set(
                    f'user_settings_{uid}',
                    json.dumps(result[uid]),
                    ex=3600
                )
        
        return result

    async def _get_first_attr(queryset, attr_name):
        """Helper to get first item's attribute from async queryset"""
        if item := await queryset.afirst():
            return getattr(item, attr_name, None)
        return None

    async def get_associated_branch(self):
        """Returns the most relevant branch based on role and relationships."""
        cache_key = f'user_branch_{self.id}'
        redis_client = Redis.from_url('redis://localhost:6379')
        branch_id = await redis_client.get(cache_key)
        if branch_id:
            return await Branch.objects.aget(id=int(branch_id))

        role_value = await self.get_role_value()
        # Non-branch roles: company_admin, restaurant_owner, country_manager, restaurant_manager
        if role_value <= 4 or not await self.branches.acount():
            return None
        
        branch = await self.branches.afirst()
        if branch:
            await redis_client.set(cache_key, branch.id, ex=3600)
        return branch

    async def get_associated_restaurant(self):
        """Returns the most relevant restaurant based on role and relationships."""
        cache_key = f'user_restaurant_{self.id}'
        redis_client = Redis.from_url('redis://localhost:6379')
        restaurant_id = await redis_client.get(cache_key)
        if restaurant_id:
            return await Restaurant.objects.aget(id=int(restaurant_id))

        role_value = await self.get_role_value()
        if role_value in [1, 4]:  # restaurant_owner, restaurant_manager
            # Check owner or managers fields first
            if self.role == 'restaurant_owner':
                restaurant = await Restaurant.objects.filter(owner=self).afirst()
            else:  # restaurant_manager
                restaurant = await Restaurant.objects.filter(managers=self).afirst()
            if restaurant:
                await redis_client.set(cache_key, restaurant.id, ex=3600)
                return restaurant
        # Fallback to user.restaurants
        restaurant = await self.restaurants.afirst()
        if restaurant:
            await redis_client.set(cache_key, restaurant.id, ex=3600)
        return restaurant

    
    async def asave(self, *args, **kwargs):
        if not self.username:  # If username is not set
            self.username = uuid.uuid4().hex[:10]  # Generate a random username
        if hasattr(self, 'role'):
            self.r_val = await self.get_role_value()
        await super().asave(*args, **kwargs)

    def __str__(self):
        return self.username

    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')

        indexes = [
            models.Index(fields=['role']),
            models.Index(fields=['timezone']),
            models.Index(fields=['preferred_language']),
        ]

STATUS_CHOICES = (
    ('active', _('Active')),
    ('inactive', _('Inactive')),
    ('suspended', _('Suspended')),
    ('pending', _('Pending Approval')),
)

class Company(models.Model):
    name = models.CharField(max_length=255, unique=True)
    about = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    contact_email = models.EmailField(blank=True, null=True)
    contact_phone = models.CharField(max_length=15, blank=True, null=True)
    created_by = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="created_company")
    default_language = models.CharField(max_length=10, choices=settings.LANGUAGES, default='en',
                                help_text=_('Default language for the company.'))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Country(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=3, unique=True)  # ISO 3166-1 alpha-3 code
    currency = models.CharField(max_length=100, blank=True, null=True)
    timezone = models.CharField(max_length=100, blank=True, null=True)
    language = models.CharField(max_length=100, blank=True, null=True)
    continent = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class RegionOrState(models.Model):
    name = models.CharField(max_length=100)
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name='regions_or_states')
    type = models.CharField(
        max_length=100,
        choices=(
            ('region', _('Region')),
            ('state', _('State')),
            ('province', _('Province')),
        ),
        default='region'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('name', 'country', 'type')  # Prevent duplicate names for the same type in a country

    def __str__(self):
        return f"{self.name} ({self.type})"


class City(models.Model):
    name = models.CharField(max_length=100)
    region_or_state = models.ForeignKey(RegionOrState, on_delete=models.CASCADE, related_name='cities')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('name', 'region_or_state')  # Prevent duplicate city names in a region/state

    def __str__(self):
        return self.name


class Restaurant(models.Model):
    name = models.CharField(max_length=100)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name='restaurants')  
    address = models.TextField()
    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name='restaurants')
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name='restaurants')
    region_or_state = models.ForeignKey(RegionOrState, null=True, on_delete=models.CASCADE, related_name='restaurants')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    manager = models.ForeignKey( settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="managed_restaurant",
        help_text="User assigned as the manager of this restaurant",
    )
    is_active = models.BooleanField(default=True)
    default_language = models.CharField(max_length=10, choices=settings.LANGUAGES, default=None, blank=True, null=True,
                                help_text=_('Default language for the restaurant. Falls back to company language.'))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE, related_name="restaurant")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    async def get_effective_language(self):
        """Returns restaurant’s language or falls back to company or country."""
        if self.default_language:
            return self.default_language
        if self.company:
            return self.company.default_language
        country = await Country.objects.filter(restaurants=self).afirst()
        return country.default_language if country else 'en'

    def __str__(self):
        return self.name


class Branch(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='branches')
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name='branches')  # Required, as branches belong to a company
    name = models.CharField(max_length=100)  # Name of the branch (e.g., "Downtown Branch")
    address = models.TextField()
    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name='branches')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name='branches')
    manager = models.ForeignKey( settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="managed_branch",
        help_text="User assigned as the manager of this branch",
    )
    is_active = models.BooleanField(default=True)
    timezone = models.CharField(max_length=50, choices=[(tz, tz) for tz in pytz.common_timezones], default='UTC')
    default_language = models.CharField(max_length=10, choices=settings.LANGUAGES, default=None, blank=True, null=True,
                                help_text=_('Default language for the branch. Falls back to restaurant or company language.'))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE, related_name="branch")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _country_code = None  # Instance-level cache
    
    @property
    async def acountry_code(self):
        if self._country_code is None:
            # Only hits DB once per instance lifetime
            self._cached_country_code = self.country.code  
        return self._cached_country_code


    async def get_effective_language(self):
        """Returns branch’s language or falls back to restaurant/company language."""
        if self.default_language:
            return self.default_language
        restaurant = await Restaurant.objects.aget(id=self.restaurant_id)
        return await restaurant.get_effective_language()
    
    async def get_active_users(
        self,
        return_instances: bool = False,
        roles: list[str] | None = None,
        only_fields: list[str] | None = None,
        user_ids: list[int] | None = None,
        order_by: list[str] | None = ['id'],  # Default ordering for reliable batching
        batch_size: int | None = None,
    ) -> Union[List[Union[int, 'CustomUser']], AsyncGenerator[Union[int, 'CustomUser'], None]]:
        """
        Get active users with single-query efficiency.

        Args:
            return_instances: Return full instances or just IDs
            roles: Filter by role names
            only_fields: Fields to select (when return_instances=True)
            order_by: Fields to order by

        Returns:
            List of IDs or User instances

        Examples:
            # Get IDs with role filter
            user_ids = await branch.get_active_users(roles=['cashier'])
            
            # Get instances with field selection
            users = await branch.get_active_users(
                return_instances=True,
                only_fields=['id', 'role'],
                order_by=['id']
            )
        """
        print("roles, only_fields, user_ids: ", roles, only_fields, user_ids)
        # Build base queryset
        queryset = self.employees.filter(is_active=True)

        # Apply combined filters
        if roles and user_ids:
            queryset = queryset.filter(
                models.Q(role__in=roles) &
                models.Q(id__in=user_ids)
            )
        
        # Apply filters
        elif roles:
            queryset = queryset.filter(role__in=roles)
        elif user_ids:
            queryset = queryset.filter(id__in=user_ids)
        
        # Configure return type
        if return_instances and only_fields:
            queryset = queryset.only(*only_fields)
        elif not return_instances:
            queryset = queryset.only('id')

        if order_by:
            queryset = queryset.order_by(*order_by)

        batch = []
        async for user in queryset:
            batch.append(user if return_instances else user.id)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        
        if batch:
            yield batch
        
        def __str__(self):
            return f"{self.name} - Restau#{self.restaurant_id}"
        
    # models.Index(fields=['manager']), 


class Menu(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='menus')
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    description = models.TextField(blank=True, null=True, verbose_name=_("Description"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='created_menus')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Menu")
        verbose_name_plural = _("Menus")
        indexes = [models.Index(fields=['branch'])]


class MenuCategory(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    description = models.TextField(blank=True, null=True, verbose_name=_("Description"))
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name='categories')
    is_active = models.BooleanField(default=True) # soft-delete approach
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='created_menucategories')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Menu Category")
        verbose_name_plural = _("Menu Categories")
        indexes = [models.Index(fields=['menu'])]


class MenuItem(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    description = models.TextField(blank=True, null=True, verbose_name=_("Description"))
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Price"))
    is_available = models.BooleanField(default=True, verbose_name=_("Available"))
    is_active = models.BooleanField(default=True)
    image = models.ImageField(upload_to='menu_items/', blank=True, null=True, verbose_name=_("Image"))
    category = models.ForeignKey(MenuCategory, on_delete=models.CASCADE, related_name='items', verbose_name=_("Category"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='created_menuitems')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Menu Item")
        verbose_name_plural = _("Menu Items")
        indexes = [models.Index(fields=['category'])]


class Order(models.Model):

    ORDER_STATUS_CHOICES = [
        ('received', _("Received")), # Food Runner places the order and sends to kitchen
        ('preparing', _("Preparing")), # When cook claims order state changes to prepping
        ('ready', _("Ready to Serve")), # Cook marks the order as completed -> foodrunner claims serve task
        ('delivered', _("Delivered")), # Foodrunner delivers food on table
        ('completed', _("Completed")), # Cashier processes payment for the order -> it is marked as completed
        ('canceled', _("Canceled")),
    ]

    ORDER_TYPE_CHOICES = [
        ('dine_in', _("Dine-in")),
        ('takeaway', _("Takeaway")),
        ('delivery', _("Delivery")),
    ]

    SOURCE_CHOICES = [
        ('web', _("Website")),
        ('app', _("Mobile App")),
        ('pos', _("POS Terminal")),
        ('kiosk', _("In-Store Kiosk")),
    ]

    # Add version for optimistic locking
    version = models.IntegerField(default=0)

    status = models.CharField(max_length=20, choices=ORDER_STATUS_CHOICES, default='received', verbose_name=_("Status"))
    order_number = models.CharField(max_length=50, unique=True)
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default='dine_in', verbose_name=_("Order Type"))
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='web', verbose_name=_("Source"))
    table_number = models.CharField(max_length=10, blank=True, null=True)  # For dine-in
    delivery_driver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='deliveries'
    )
    branch = models.ForeignKey('Branch', on_delete=models.CASCADE, related_name='orders', verbose_name=_("Branch"))
    total_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Total Price"))
    special_instructions = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created At"))
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='created_orders')
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='deleted_orders')

    def __str__(self):
        return f"{_('Order')} {self.id} - {self.get_status_display()}"

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        indexes = [
            models.Index(fields=['branch', 'status', 'total_price'])
        ]


class OrderItem(models.Model):
    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='items', verbose_name=_("Order"))
    menu_item = models.ForeignKey('MenuItem', on_delete=models.CASCADE, related_name='order_items', verbose_name=_("Menu Item"))
    quantity = models.PositiveIntegerField(default=1, verbose_name=_("Quantity"))
    item_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Item Price"))
    is_active = models.BooleanField(default=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='added_orderitems')
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='deleted_orderitems')

    def __str__(self):
        return f"{self.menu_item.name} x {self.quantity}"

    class Meta:
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")
        indexes = [
            models.Index(fields=['order'])
        ]


class Shift(models.Model):
    """Defines a shift template for a branch (e.g., Morning, Evening)."""
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='shifts')
    name = models.CharField(max_length=50)  # e.g., "Morning Shift"
    start_time = models.TimeField()  # e.g., 08:00
    end_time = models.TimeField()    # e.g., 16:00

    def __str__(self):
        return f"{self.name} at {self.branch}"


class StaffShift(models.Model):
    """Assigns a user to a shift instance on a specific date."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='staff_shifts')
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE)
    date = models.DateField()  # Specific date of the shift
    start_datetime = models.DateTimeField(null=True, blank=True)   # Computed: date + shift.start_time
    end_datetime = models.DateTimeField(null=True, blank=True)   # Computed: date + shift.end_time
    overtime_end_datetime = models.DateTimeField(null=True, blank=True)  # Extended end time if approved
    is_overtime_approved = models.BooleanField(default=False)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.CASCADE, related_name='staff_shifts')

    class Meta:
        unique_together = ('user', 'shift', 'date')
        indexes = [
            models.Index(fields=['user', 'start_datetime', 'end_datetime']),
        ]

    async def asave(self, *args, **kwargs):
        """Compute UTC datetimes from branch local time."""
        staff_shift = await StaffShift.objects.select_related('shift__branch').aget(id=self.id)
        shift = staff_shift.shift
        branch = shift.branch
        branch_tz = pytz.timezone(branch.timezone)
        naive_start = timezone.datetime.combine(self.date, shift.start_time)
        naive_end = timezone.datetime.combine(self.date, shift.end_time)
        self.start_datetime = branch_tz.localize(naive_start).astimezone(pytz.UTC)
        self.end_datetime = branch_tz.localize(naive_end).astimezone(pytz.UTC)
        await super().asave(*args, **kwargs)

    def is_active(self):
        """Check if shift is active in UTC time."""
        now = timezone.now()
        return self.start_datetime <= now <= (self.overtime_end_datetime or self.end_datetime)

    def is_overtime_active(self):
        """Check if overtime is active in UTC time."""
        now = timezone.now()
        return (
            self.overtime_end_datetime and
            self.end_datetime < now <= self.overtime_end_datetime and
            self.is_overtime_approved
        )

    async def extend_overtime(self, extra_hours, staff_shift):
        """Extend shift with overtime, storing in UTC."""
        self = await StaffShift.objects.select_related('branch', 'shift').aget(id=self.id)
        branch_tz = pytz.timezone(self.branch.timezone)
        # today = timezone.now().astimezone(branch_tz).date()
        if self.end_datetime is not None:
            # Option 1: Use end_datetime
            self.overtime_end_datetime = self.end_datetime + timezone.timedelta(hours=extra_hours)
        else:
            # Option 2: Combine shift.end_time with today
            naive_end_time = timezone.datetime.combine(self.date, self.shift.end_time)
            localized_end_time = branch_tz.localize(naive_end_time)
            self.overtime_end_datetime = localized_end_time + timezone.timedelta(hours=extra_hours)

        self.is_overtime_approved = True
        await self.asave()

    def __str__(self):
        return f"{self.user.username} - {self.shift.name} on {self.date}"
    

class ShiftPattern(models.Model):
    class PatternType(models.TextChoices):
        ROLE_BASED = 'RB', _('Role-Based')
        USER_SPECIFIC = 'US', _('User-Specific')
        ROTATING = 'RT', _('Rotating')
        AD_HOC = 'AH', 'Ad-Hoc'
        HYBRID = 'HY', 'Hybrid'
    
    # What this pattern applies to (role, user, or both)
    roles = ArrayField(
        models.CharField(max_length=30, choices=CustomUser.ROLE_CHOICES),
        size = 10,  # Limit to 10 roles for performance
        default = list,
        null=True,
        blank=True,
        help_text = _("List of roles this pattern applies to")
    )
    users = ArrayField(
        models.IntegerField(),
        default = list,
        blank = True,
        null=True,
        help_text = _("List of user IDs this pattern applies to")
    )
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    
    pattern_type = models.CharField(max_length=2, choices=PatternType.choices)
    config = models.JSONField()  # Type-specific configuration
    priority = models.IntegerField(default=1)  # Higher overrides lower
    
    active_from = models.DateField()
    active_until = models.DateField(null=True, blank=True)
    is_temp = models.BooleanField(default=False)  # For short-term overrides
    
    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(roles__len__gt=0) | models.Q(users__len__gt=0),
                name='at_least_one_target'
            )
        ]
        indexes = [
            models.Index(fields=['branch', 'active_from', 'active_until']),
            models.Index(fields=['roles'], name='roles_idx'),
            models.Index(fields=['users'], name='users_idx')
        ]


class StaffAvailability(models.Model):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('busy', 'Busy'), 
        ('break', 'On Break'),
        ('offline', 'Offline'),
        ('overtime', 'Overtime'),
    ]
    
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='availability')
    current_task = models.ForeignKey('notifications.Task', null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='offline')
    last_update = models.DateTimeField(auto_now=True)

    async def current_shift(self):
        """Get the user's current active shift."""
        return await StaffShift.objects.filter(
            user=self.user,
            start_datetime__lte=timezone.now(),
            end_datetime__gte=timezone.now()
        ).afirst()

    async def update_status(self):
        """Update status based on shift and task state."""
        shift = await self.current_shift()
        now = timezone.now()
        if not shift:
            recent_shift = await StaffShift.objects.filter(
                user=self.user,
                end_datetime__lt=now
            ).order_by('-end_datetime').afirst()
            if (
                recent_shift and
                self.current_task and
                self.current_task.status in ('pending', 'claimed') and  # Task still active
                now < recent_shift.end_datetime + timezone.timedelta(minutes=10)
            ):
                self.status = 'busy'  # Within 10-min post-shift window
            else:
                self.status = 'offline'
        elif shift.is_overtime_active():
            self.status = 'overtime'
        elif self.current_task and self.current_task.status in ('pending', 'claimed'):
            self.status = 'busy'
        elif shift.is_active():
            self.status = 'available'
        else:
            self.status = 'offline'
        await self.asave()

    def __str__(self):
        return f"{self.user.username} - {self.status}"


class OvertimeRequest(models.Model):
    """User-initiated overtime request."""
    staff_shift = models.ForeignKey(StaffShift, on_delete=models.CASCADE, related_name='overtime_requests')
    requested_hours = models.FloatField()  # e.g., 1.5 hours
    reason = models.TextField()
    is_approved = models.BooleanField(default=False)
    requested_at = models.DateTimeField(default=timezone.now)
    manager_response_at = models.DateTimeField(null=True, blank=True)

    async def approve(self):
        """Manager approves the request."""
        branch_tz = pytz.timezone(self.staff_shift.branch.timezone)
        today = timezone.now().astimezone(branch_tz).date()
        if self.is_approved == True:
            raise ValidationError(_("Overtime  already approved."))
        if self.staff_shift.date < today:
            raise ValidationError(_("Cannot approve overtime for a shift that has already ended."))
        self.is_approved = True
        self.manager_response_at = timezone.now()
        # print(f"branch_tz - today: {branch_tz} - {today}")
        await self.staff_shift.extend_overtime(self.requested_hours, self.staff_shift)
        await self.asave()

    def __str__(self):
        return f"{self.staff_shift.user.username} - {self.requested_hours} hours"
    

class SequenceCounter(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    sequence_type = models.CharField(max_length=50)
    max_digits = models.PositiveSmallIntegerField(default=4)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('branch', 'sequence_type')
        indexes = [
            models.Index(fields=['branch', 'sequence_type']),
        ]