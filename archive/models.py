from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

CustomUser = get_user_model()
class DeletedObject(models.Model):
    object_type = models.CharField(max_length=50, choices=[
        ('company', 'Company'),
        ('restaurant', 'Restaurant'),
        ('branch', 'Branch')
    ])
    object_id = models.PositiveIntegerField()
    object_map = models.JSONField(default=dict)
    grace_period_expiry = models.DateTimeField()
    deleted_on = models.DateTimeField(default=timezone.now)
    deleted_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='deleted_objects')
    reverted_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='reverted_objects')
    reverted_at = models.DateTimeField(null=True, blank=True)
    cleanup_task_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', 'Pending Deletion'),
        ('deleted', 'Deleted'),
        ('reverted', 'Reverted')
    ], default='pending_deletion')
    migration_status = models.CharField(max_length=20, choices=[
        ('not_migrated', 'Not Migrated'),
        ('migrated', 'Migrated'),
        ('failed', 'Failed')
    ], default='not_migrated')

    class Meta:
        indexes = [
            models.Index(fields=['object_type', 'object_id']),
            models.Index(fields=['status', 'migration_status'])
        ]

    def __str__(self):
        return f"{self.object_type} ({self.status})"


class ObjectHistory(models.Model):
    object_type = models.CharField(max_length=50, choices=[
        ('company', 'Company'),
        ('restaurant', 'Restaurant'),
        ('branch', 'Branch')
    ])
    object_id = models.PositiveIntegerField()
    action = models.CharField(max_length=50)  # e.g., "pending_deletion", "reverted", "migrated"
    user_id = models.PositiveIntegerField(null=True) # modify to user_id in signals.py
    timestamp = models.DateTimeField(default=timezone.now)
    deleted_object = models.ForeignKey(DeletedObject, on_delete=models.SET_NULL, null=True, related_name='history')
    details = models.TextField(blank=True)  # e.g., "Migrated 5 branches"

    class Meta:
        indexes = [
            models.Index(fields=['object_type', 'object_id']),
            models.Index(fields=['timestamp'])
        ]

    def __str__(self):
        return f"{self.object_type} {self.object_id} - {self.action} at {self.timestamp}"