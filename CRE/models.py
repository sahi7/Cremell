import uuid
import pytz

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.conf import settings
from django.db import models

class CustomUserManager(BaseUserManager):
    def create_user(self, email=None, username=None, phone_number=None, password=None, **extra_fields):
        if not email and not username and not phone_number:
            raise ValueError(_('The Email, Username, or Phone number field must be set'))
        
        email = self.normalize_email(email) if email else None
        user = self.model(email=email, username=username, phone_number=phone_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email=None, username=None, phone_number=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, username, phone_number, password, **extra_fields)

    def create_user_with_role(self, role, email, phone_number, password=None, **extra_fields):
        if not email and not phone_number:
            raise ValueError(_('The Email or Phone number field must be set'))
        if not role:
            raise ValueError(_('A role must be specified when creating a user'))
        extra_fields.setdefault('role', role)
        email = self.normalize_email(email) if email else None
        if role in ['company_admin', 'restaurant_owner']:
            extra_fields.setdefault('is_staff', True)
        return self.create_user(email=email, phone_number=phone_number, password=password, **extra_fields)

    # Role-specific convenience method
    def create_delivery_man(self, email=None, phone_number=None, password=None, **extra_fields):
        return self.create_user_with_role('delivery_man', email=email, phone_number=phone_number, password=password, **extra_fields)

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
        ('active', _('Active')),
        ('suspended', _('Suspended')),
        ('on_leave', _('On Leave')),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', blank=True, null=True)
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, blank=True, null=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    hire_date = models.DateField(blank=True, null=True)
    bio = models.TextField(blank=True, null=True)

    objects = CustomUserManager()

    def get_role_value(self, role=None):
        """
        Map the role string to a numeric value for comparison.
        """
        role_hierarchy = {
            'company_admin': 1,
            'restaurant_owner': 2,
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
        return role_hierarchy.get(role or self.role, 0)  # If no role is specified, default to the instance's role

    def add_to_group(self, role):
        """
        Add the user to the corresponding group based on their role.
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
            group = Group.objects.get(name=group_name)
            self.groups.add(group)

    
    def save(self, *args, **kwargs):
        if not self.username:  # If username is not set
            self.username = uuid.uuid4().hex[:10]  # Generate a random username
        super().save(*args, **kwargs)

    def __str__(self):
        return self.username

    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')

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
    created_by = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="created_company"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Country(models.Model):
    # usa = Country.objects.create(name="United States", code="USA")
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
    # california = RegionOrState.objects.create(name="California", country=usa, type="state")
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
    # los_angeles = City.objects.create(name="Los Angeles", region_or_state=california)
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
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.CASCADE, related_name='restaurants'
    )  # Company is optional for standalone restaurants
    address = models.TextField()
    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name='restaurants')
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name='restaurants')
    region_or_state = models.ForeignKey(RegionOrState, null=True, on_delete=models.CASCADE, related_name='restaurants')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    manager = models.ForeignKey( settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="managed_restaurant",
        help_text="User assigned as the manager of this restaurant",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE, related_name="restaurant")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
    timezone = models.CharField(max_length=50, choices=[(tz, tz) for tz in pytz.common_timezones], default='UTC')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE, related_name="branch")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.restaurant.name}" 

    def local_now(self):
        """Get current time in branch's timezone."""
        return timezone.localtime(timezone.now(), pytz.timezone(self.timezone))


class Menu(models.Model):
    branches = models.ManyToManyField(Branch, related_name='menus')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='menus', null=True, blank=True)
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    description = models.TextField(blank=True, null=True, verbose_name=_("Description"))
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Menu")
        verbose_name_plural = _("Menus")


class MenuCategory(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    description = models.TextField(blank=True, null=True, verbose_name=_("Description"))
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name='categories')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Menu Category")
        verbose_name_plural = _("Menu Categories")


class MenuItem(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("Name"))
    description = models.TextField(blank=True, null=True, verbose_name=_("Description"))
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Price"))
    is_available = models.BooleanField(default=True, verbose_name=_("Available"))
    image = models.ImageField(upload_to='menu_items/', blank=True, null=True, verbose_name=_("Image"))
    category = models.ForeignKey( MenuCategory, on_delete=models.CASCADE, related_name='menu_items', verbose_name=_("Category"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Menu Item")
        verbose_name_plural = _("Menu Items")


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
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default='dine_in', verbose_name=_("Order Type"))
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='web', verbose_name=_("Source"))

    table_number = models.CharField(max_length=10, blank=True, null=True)  # For dine-in
    delivery_driver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='deliveries'
    )

    branch = models.ForeignKey('Branch', on_delete=models.CASCADE, related_name='orders', verbose_name=_("Branch"))

    status = models.CharField(max_length=20, choices=ORDER_STATUS_CHOICES, default='Received', verbose_name=_("Status"))
    total_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Total Price"))
    special_instructions = models.TextField(blank=True)
    modified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='modified_orders')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created At"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated At"))

    def __str__(self):
        return f"{_('Order')} {self.id} - {self.get_status_display()}"

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")


class OrderItem(models.Model):
    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='order_items', verbose_name=_("Order"))
    menu_item = models.ForeignKey('MenuItem', on_delete=models.CASCADE, related_name='order_items', verbose_name=_("Menu Item"))
    quantity = models.PositiveIntegerField(default=1, verbose_name=_("Quantity"))
    item_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Item Price"))

    def __str__(self):
        return f"{self.menu_item.name} x {self.quantity}"

    class Meta:
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")


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
    start_datetime = models.DateTimeField()  # Computed: date + shift.start_time
    end_datetime = models.DateTimeField()    # Computed: date + shift.end_time
    overtime_end_datetime = models.DateTimeField(null=True, blank=True)  # Extended end time if approved
    is_overtime_approved = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'shift', 'date')
        indexes = [
            models.Index(fields=['user', 'start_datetime', 'end_datetime']),
        ]

    def save(self, *args, **kwargs):
        """Compute UTC datetimes from branch local time."""
        branch_tz = pytz.timezone(self.shift.branch.timezone)
        naive_start = timezone.datetime.combine(self.date, self.shift.start_time)
        naive_end = timezone.datetime.combine(self.date, self.shift.end_time)
        self.start_datetime = branch_tz.localize(naive_start).astimezone(pytz.UTC)
        self.end_datetime = branch_tz.localize(naive_end).astimezone(pytz.UTC)
        super().save(*args, **kwargs)

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

    def extend_overtime(self, extra_hours):
        """Extend shift with overtime, storing in UTC."""
        if not self.overtime_end_datetime:
            self.overtime_end_datetime = self.end_datetime + timezone.timedelta(hours=extra_hours)
            self.is_overtime_approved = True
            self.save()

    def __str__(self):
        return f"{self.user.username} - {self.shift.name} on {self.date}"


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

    def current_shift(self):
        """Get the user's current active shift."""
        return StaffShift.objects.filter(
            user=self.user,
            start_datetime__lte=timezone.now(),
            end_datetime__gte=timezone.now()
        ).first()

    def update_status(self):
        """Update status based on shift and task state."""
        shift = self.current_shift()
        now = timezone.now()
        if not shift:
            recent_shift = StaffShift.objects.filter(
                user=self.user,
                end_datetime__lt=now
            ).order_by('-end_datetime').first()
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
        self.save()

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

    def approve(self):
        """Manager approves the request."""
        self.is_approved = True
        self.manager_response_at = timezone.now()
        self.staff_shift.extend_overtime(self.requested_hours)
        self.save()

    def __str__(self):
        return f"{self.staff_shift.user.username} - {self.requested_hours} hours"