from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

class Task(models.Model):
    TASK_TYPES = [
        ('prepare', 'Prepare Order'),
        ('serve', 'Serve Order'),
        ('payment', 'Process Payment')
    ]
    
    order = models.ForeignKey('CRE.Order', on_delete=models.CASCADE)
    task_type = models.CharField(max_length=20, choices=TASK_TYPES)
    status = models.CharField(max_length=20, choices=[
        ('pending', 'Pending'),
        ('claimed', 'Claimed'),
        ('completed', 'Completed'),
        ('escalated', 'Escalated')
    ])
    claimed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    preparation_time = models.DurationField(null=True, blank=True)
    version = models.IntegerField(default=0)
    timeout_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Auto-calculate timings
        if self.status == 'completed':
            if self.task_type == 'prepare':
                self.preparation_time = timezone.now() - self.created_at
            elif self.task_type == 'serve':
                self.delivery_time = timezone.now() - self.created_at
        super().save(*args, **kwargs)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'task_type']),
            models.Index(fields=['timeout_at']),
        ]

    def __str__(self):
        return f"{self.task_type} for Order {self.order.id} - {self.status}"

class BroadcastChannel(models.Model):
    CHANNEL_TYPES = [
        ('kitchen', 'Kitchen'),
        ('service', 'Service'),
        ('management', 'Management')
    ]
    
    branch = models.ForeignKey('CRE.Branch', on_delete=models.CASCADE)
    channel_type = models.CharField(max_length=20, choices=CHANNEL_TYPES)
    active_connections = models.PositiveIntegerField(default=0)


class BranchActivity(models.Model):
    """Logging activity within a branch"""
    ACTIVITY_CHOICES = [
        ('order_create', 'Order Created'),
        ('order_modify', 'Order Modified'),
        ('task_claim', 'Task Claimed'),
        ('task_complete', 'Task Completed'),
        ('payment_process', 'Payment Processed'),
        ('staff_available', 'Staff Availability Changed'),
        ('escalation', 'Task Escalated')
    ]
    
    branch = models.ForeignKey('CRE.Branch', on_delete=models.CASCADE)
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_CHOICES)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    timestamp = models.DateTimeField(auto_now_add=True)
    details = models.JSONField()

    class Meta:
        indexes = [
            models.Index(fields=['-timestamp']),
            models.Index(fields=['activity_type']),
        ]

    def __str__(self):
        return f"{self.get_activity_type_display()} @ {self.timestamp}"


class EmployeeTransfer(models.Model):
    """
    Model to track employee transfers between branches or restaurants.
    """
    TRANSFER_TYPES = (
        ('temporary', _('Temporary')),
        ('permanent', _('Permanent')),
    )
    STATUS_CHOICES = (
        ('pending', _('Pending')),
        ('approved', _('Approved')),
        ('rejected', _('Rejected')),
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transfers', verbose_name=_('User'))
    from_branch = models.ForeignKey('CRE.Branch', null=True, blank=True, on_delete=models.SET_NULL, related_name='transfers_from', verbose_name=_('From Branch'))
    to_branch = models.ForeignKey('CRE.Branch', on_delete=models.CASCADE, related_name='transfers_to', verbose_name=_('To Branch'))
    from_restaurant = models.ForeignKey('CRE.Restaurant', null=True, blank=True, on_delete=models.SET_NULL, related_name='transfers_out', verbose_name=_('From Restaurant'))
    to_restaurant = models.ForeignKey('CRE.Restaurant', null=True, blank=True, on_delete=models.SET_NULL, related_name='transfers_in', verbose_name=_('To Restaurant'))
    transfer_type = models.CharField(max_length=10, choices=TRANSFER_TYPES, verbose_name=_('Transfer Type'))
    start_date = models.DateTimeField(auto_now_add=True, verbose_name=_('Start Date'))
    end_date = models.DateTimeField(null=True, blank=True, verbose_name=_('End Date'))  # Null for permanent
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='initiated_transfers', verbose_name=_('Initiated By'))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', verbose_name=_('Status'))

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['to_branch', 'start_date']),
        ]


class TransferHistory(models.Model):
    """
    Model to log historical transfer events for auditing and tracking.
    Each transfer action (approve/revert) logs to TransferHistory, accessible via branch.transfer_history.all() or restaurant.transfer_history.all().
    """
    TRANSFER_TYPES = (
        ('temporary', _('Temporary')),
        ('permanent', _('Permanent')),
    )
    STATUS_CHOICES = (
        ('approved', _('Approved')),
        ('rejected', _('Rejected')),
    )
    user = models.ForeignKey('CRE.CustomUser', on_delete=models.CASCADE, related_name='transfer_history', verbose_name=_("User"))
    branch = models.ForeignKey('CRE.Branch', null=True, blank=True, on_delete=models.SET_NULL, related_name='transfer_history', verbose_name=_("Branch"))
    restaurant = models.ForeignKey('CRE.Restaurant', null=True, blank=True, on_delete=models.SET_NULL, related_name='transfer_history', verbose_name=_("Restaurant"))
    transfer_type = models.CharField(max_length=10, choices=TRANSFER_TYPES, verbose_name=_("Transfer Type"))
    from_entity = models.CharField(max_length=100, verbose_name=_("From Entity"))  # e.g., "Branch #5" or "Restaurant #1"
    to_entity = models.CharField(max_length=100, verbose_name=_("To Entity"))  # e.g., "Branch #6" or "Restaurant #2"
    initiated_by = models.ForeignKey('CRE.CustomUser', on_delete=models.SET_NULL, null=True, related_name='initiated_transfer_history', verbose_name=_("Initiated By"))
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name=_("Timestamp"))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, verbose_name=_("Status"))
    end_date = models.DateTimeField(null=True, blank=True, verbose_name=_("End Date"))  # For temporary transfers

    class Meta:
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['branch', 'timestamp']),
            models.Index(fields=['restaurant', 'timestamp']),
        ]
        verbose_name = _("Transfer History")
        verbose_name_plural = _("Transfer Histories")