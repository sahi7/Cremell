from django.db import models
from django.utils.translation import gettext as _

from cre.models import Branch

class Printer(models.Model):
    """
    Represents a printer configuration for a specific branch.
    Supports dynamic addition of printers per branch, with details for connection.
    """
    CONNECTION_TYPES = (
        ('usb', _('USB')),
        ('network', _('Network')),
        ('serial', _('Serial')),
    )

    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='printers', help_text=_("Branch this printer belongs to"))
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
        verbose_name = _("Printer")
        verbose_name_plural = _("Printers")
        unique_together = ('branch', 'name')
        ordering = ['branch', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_connection_type_display()}) at {self.branch.name}"