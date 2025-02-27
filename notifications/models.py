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
    created_at = models.DateTimeField(auto_now_add=True)
    timeout_at = models.DateTimeField()
    version = models.IntegerField(default=0)

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


class StaffAvailability(models.Model):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('busy', 'Busy'), 
        ('break', 'On Break'),
        ('offline', 'Offline')
    ]
    
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    current_task = models.ForeignKey('Task', null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    last_update = models.DateTimeField(auto_now=True)