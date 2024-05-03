import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models

class CustomUser(AbstractUser):
    GENDER_CHOICES = (
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    )
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    date_of_birth = models.DateField()
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    is_active = models.BooleanField(default=True)

    # Contact Information
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20)
    address_line_1 = models.CharField(max_length=255)
    address_line_2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=100)

    # Role-specific details
    ROLE_CHOICES = (
        ('manager', 'Manager'),
        ('chef', 'Chef'),
        ('server', 'Server'),
        ('bartender', 'Bartender'),
    )
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('suspended', 'Suspended'),
        ('on_leave', 'On Leave'),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    salary = models.DecimalField(max_digits=10, decimal_places=2)
    hire_date = models.DateField()
    bio = models.TextField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.username:  # If username is not set
            self.username = uuid.uuid4().hex[:10]  # Generate a random username
        super().save(*args, **kwargs)

    def __str__(self):
        return self.username


# How can this models be used with django all-auth and serializers in DRF