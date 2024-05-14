import uuid
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _
from django.db import models

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
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    address_line_1 = models.CharField(max_length=255, blank=True, null=True)
    address_line_2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    country = models.CharField(max_length=100)

    # Role-specific details
    ROLE_CHOICES = (
        ('manager', _('Manager')),
        ('chef', _('Chef')),
        ('server', _('Server')),
        ('bartender', _('Bartender')),
    )
    STATUS_CHOICES = (
        ('active', _('Active')),
        ('suspended', _('Suspended')),
        ('on_leave', _('On Leave')),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', blank=True, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, blank=True, null=True)
    salary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    hire_date = models.DateField(blank=True, null=True)
    bio = models.TextField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.username:  # If username is not set
            self.username = uuid.uuid4().hex[:10]  # Generate a random username
        super().save(*args, **kwargs)

    def __str__(self):
        return self.username

    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')


# How can this models be used with django all-auth and serializers in DRF