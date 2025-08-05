from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from CRE.models import Branch

CustomUser = get_user_model()

# BranchPermissionPool model to define available permissions for a branch
class BranchPermissionPool(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="permission_pools")
    permissions = models.ManyToManyField(Permission, related_name="permission_pools")
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name="created_pools")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['branch'], name='unique_pool_per_branch'),
        ]
        indexes = [
            models.Index(fields=['branch']),
        ]

    def __str__(self):
        return f"Permission Pool for {self.branch}"
    
# BranchPermissionAssignment model for assigning permissions to users or roles
class BranchPermissionAssignment(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expired', 'Expired'), # reached end_time - stopped automatically by system
        ('revoked', 'Revoked'), # stoped by user
    ]
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, null=True, blank=True, related_name="permission_assignments")
    role = models.CharField(max_length=30, choices=CustomUser.ROLE_CHOICES, blank=True, null=True)
    # role = models.ForeignKey(Role, on_delete=models.CASCADE, null=True, blank=True, related_name="permission_assignments")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="permission_assignments")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="assignments")
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    conditions = models.JSONField(default=dict, blank=True)
    assigned_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name="assigned_permissions")
    assigned_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    revoked_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="revoked_permissions")
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'branch', 'permission'],
                condition=models.Q(user__isnull=False),
                name='unique_user_permission_assignment'
            ),
            models.UniqueConstraint(
                fields=['role', 'branch', 'permission'],
                condition=models.Q(role__isnull=False),
                name='unique_role_permission_assignment'
            ),
            models.CheckConstraint(
                check=models.Q(user__isnull=False, role__isnull=True) | models.Q(user__isnull=True, role__isnull=False),
                name='user_or_role_exclusive'
            ),
            models.CheckConstraint(
                check=models.Q(end_time__gt=models.F('start_time')) | models.Q(end_time__isnull=True),
                name='end_time_after_start_time'
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'branch', 'permission']),
            models.Index(fields=['role', 'branch', 'permission']),
            models.Index(fields=['start_time']),
            models.Index(fields=['end_time']),
            models.Index(fields=['status']),
        ]
