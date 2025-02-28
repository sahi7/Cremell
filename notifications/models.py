from django.db import models

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
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL
    )
    preparation_time = models.DurationField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    timeout_at = models.DateTimeField()
    version = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.task_type} for Order {self.order.id} - {self.status}"

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