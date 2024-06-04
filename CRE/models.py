import uuid
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.translation import gettext_lazy as _
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

class CustomUser(AbstractUser):
    GENDER_CHOICES = (
        ('male', _('Male')),
        ('female', _('Female')),
        ('other', _('Other')),
    )
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    is_active = models.BooleanField(default=True)

    # Contact Information
    email = models.EmailField(unique=True, blank=True, null=True)
    phone_number = models.CharField(max_length=20, unique=True, blank=True, null=True)
    address_line_1 = models.CharField(max_length=255, blank=True, null=True)
    address_line_2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    country = models.CharField(max_length=100)

    # Role-specific details
    ROLE_CHOICES = (
        ('regional_president', _('Regional President')),
        ('regional_director', _('Regional Director')),
        ('country_manager', _('Country Manager')),
        ('restaurant_manager', _('Restaurant Manager')),
        ('shift_leader', _('Shift Leader')),
        ('cook', _('Cook')),
        ('delivery_man', _('Delivery Man')),
        ('cashier', _('Cashier')),
        ('utility_worker', _('Utility Worker')),
        ('cleaner', _('Cleaner')),
        ('food_runner', _('Food Runner')),
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

    def save(self, *args, **kwargs):
        if not self.username:  # If username is not set
            self.username = uuid.uuid4().hex[:10]  # Generate a random username
        super().save(*args, **kwargs)

    def __str__(self):
        return self.username

    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')
