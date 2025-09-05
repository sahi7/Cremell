import uuid
import secrets
from django.db import models
from django.utils.translation import gettext as _
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from cre.models import Branch

CustomUser = get_user_model()


def generate_device_id():
    """Generate a 6-character unique device ID."""
    return uuid.uuid4().hex[:6].upper()

def generate_device_token():
    """Generate a secure 128-character random token."""
    return secrets.token_urlsafe(96)  # ~128 chars when encoded

def default_expiry():
    return timezone.now() + timedelta(days=10)

class Device(models.Model):
    device_id = models.CharField(max_length=6, unique=True, default=generate_device_id)
    device_token = models.CharField(max_length=128, unique=True, default=generate_device_token)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='devices')
    name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    added_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True,  related_name='devices_added')
    last_seen = models.DateTimeField(auto_now=True)
    expiry_date = models.DateTimeField(default=default_expiry)
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='device')

    class Meta:
        unique_together = ('device_id', 'branch')
        indexes = [
            models.Index(fields=['device_id']),
            models.Index(fields=['device_token', 'is_active']),
        ]

    def __str__(self):
        return f"{self.device_id} ({self.branch.name})"

class Printer(models.Model):
    """
    Represents a printer configuration for a specific device.
    Supports dynamic addition of printers per device, with details for connection.
    """
    CONNECTION_TYPES = (
        ('usb', _('USB')),
        ('network', _('Network')),
        ('serial', _('Serial')),
    )

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='printers', help_text=_("Device this printer belongs to"))
    name = models.CharField(max_length=255, help_text=_("Descriptive name for the printer, e.g., 'Kitchen Printer 1'"))
    connection_type = models.CharField(max_length=10, choices=CONNECTION_TYPES, help_text=_("Type of connection to the printer"))
    vendor_id = models.CharField(max_length=10, blank=True, null=True, help_text=_("USB vendor ID in hex, e.g., '0x04b8' (required for USB)"))
    product_id = models.CharField(max_length=10, blank=True, null=True, help_text=_("USB product ID in hex, e.g., '0x0202' (required for USB)"))
    ip_address = models.GenericIPAddressField(blank=True, null=True, help_text=_("IP address for network printers (required for network)"))
    serial_port = models.CharField(max_length=255, blank=True, null=True, help_text=_("Serial port path, e.g., '/dev/ttyS0' (required for serial)"))
    profile = models.CharField(max_length=50, default='TM-T88III', help_text=_("ESC/POS profile for the printer model, e.g., 'TM-T88III'"))
    is_active = models.BooleanField(default=True, help_text=_("Whether the printer is currently active and available"))
    last_connected = models.DateTimeField(blank=True, null=True, help_text=_("Last time the printer was successfully connected"))

    class Meta:
        unique_together = ('device', 'name')
        ordering = ['device', 'name']
        indexes = [
            models.Index(fields=['connection_type']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_connection_type_display()})"