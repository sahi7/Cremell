from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db import transaction
from .models import *
from django.contrib.auth.models import Permission
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from CRE.tasks import log_activity, send_shift_notifications
from CRE.models import Branch

CustomUser = get_user_model()
channel_layer = get_channel_layer()
@shared_task
def create_permission_assignments(assignments, assigned_by_id, branch_id, role_name_ids=None, log_details=None):
    with transaction.atomic():
        branch = Branch.objects.get(id=branch_id)
        assigned_by = CustomUser.objects.get(id=assigned_by_id)
        
        user_ids = []
        permission_ids = []
        assignment_objects = []
        for assignment in assignments:
            assignment_obj = BranchPermissionAssignment(
                user_id=assignment.get('user_id'),
                role=assignment.get('role'),
                branch_id=assignment['branch_id'],
                permission_id=assignment['permission_id'],
                start_time=assignment.get('start_time'),
                end_time=assignment.get('end_time'),
                conditions=assignment.get('conditions', {}),
                assigned_by_id=assigned_by_id
            )
            assignment_objects.append(assignment_obj)
            user_ids.append(assignment.get('user_id'))
            permission_ids.append(assignment.get('permission_id'))
            
            # Log each assignment
            details = {
                'start_time': assignment.get('start_time').isoformat() if assignment.get('start_time') else None,
                'end_time': assignment.get('end_time').isoformat() if assignment.get('end_time') else None,
                'conditions': assignment.get('conditions', {}),
                'permission_id': assignment['permission_id']
            }
            if assignment.get('user_id'):
                details['target_user_id'] = assignment['user_id']
            else:
                details['target_role'] = assignment['role']
            log_activity.delay(assigned_by_id, 'assign_permission', details, branch_id, 'branch')
        
        BranchPermissionAssignment.objects.bulk_create(assignment_objects, ignore_conflicts=True)
        if permission_ids:
            get_perms = Permission.objects.filter(id__in=permission_ids)
            perm_names = [p.name for p in get_perms]
        extra_context = {
            "permissions": perm_names
        }
        if log_details:
            extra_context.update(**log_details)
        if user_ids:
            user_ids = user_ids
        elif role_name_ids:
            user_ids = role_name_ids
        send_shift_notifications.delay(
            user_ids=user_ids, 
            branch_id=branch_id,
            subject="Permisssion assignment notification",
            message="",
            template_name="emails/permission_assignment_notification.html",
            extra_context=extra_context
            )
        groups = [
            f"user_{user_id}" for user_id in user_ids
        ]
        for group_name in groups:
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'stakeholder.notification',
                    'message': str(_(f"You have been given added permission: {perm_names}"))
                }
            )

@shared_task
def update_permission_pool(branch_id, permission_ids, created_by_id):
    from notifications.tasks import send_notification_task
    with transaction.atomic():
        branch = Branch.objects.get(id=branch_id)
        created_by = CustomUser.objects.get(id=created_by_id)
        pool, created = BranchPermissionPool.objects.get_or_create(
            branch=branch, 
            defaults={'created_by': created_by}
        )
        pool.permissions.set(permission_ids)
        
        # Log the action
        details = {'permission_ids': permission_ids, 'created': created}
        log_activity.delay(created_by_id, 'update_pool', details, branch_id, 'branch')

        get_perms = Permission.objects.filter(id__in=permission_ids)
        perm_names = _(','.join(p.name for p in get_perms))
        manager_id = branch.manager_id
        if manager_id:
            
            async_to_sync(channel_layer.group_send)(
                f'user_{manager_id}',
                {
                    'type': 'stakeholder.notification',
                    'message': str(_(f"New custom permissions available for authorization: {perm_names}"))
                }
            )
            extra_content = {
                'perm_names': perm_names,
            }
            send_notification_task.delay(
                manager_id, 
                message='',
                subject=str(_("Permission Pool Updated")),
                branch_id=branch_id,
                extra_context=extra_content,
                template_name='emails/permission_pool_update.html',
            )

from django.utils import timezone
@shared_task
def revoke_permission_assignments(assignment_ids, deleted_by_id, branch_id):
    with transaction.atomic():
        
        assignments = BranchPermissionAssignment.objects.filter(id__in=assignment_ids, status='active')
        for assignment in assignments:
            assignment.status = 'revoked'
            assignment.revoked_by_id = deleted_by_id
            assignment.revoked_at = timezone.now()
            assignment.save()
            
            # Log each revocation
            details = {
                'permission_id': assignment.permission_id,
                'status': 'revoked'
            }
            if assignment.user:
                details['target_user_id'] = assignment.user_id
                user_group = f"user_{assignment.user_id}"
                async_to_sync(channel_layer.group_send)(
                    user_group,
                    {
                        'type': 'stakeholder.notification',
                        'message': f"Your permission {assignment.permission_id} has been revoked."
                    }
                )
            else:
                details['target_role'] = assignment.role
                group_name = f"{branch_id}_{assignment.role}"
                async_to_sync(channel_layer.group_send)(
                    group_name,
                    {
                        'signal': 'revoke_permission',
                        'type': 'branch.update',
                        'message': f"Your permission {assignment.permission_id} has been revoked"
                    }
                )
            log_activity.delay(deleted_by_id, 'revoke_permission', details, branch_id, 'branch')

@shared_task
def expire_permissions():
    with transaction.atomic():
        now = timezone.now()
        assignments = BranchPermissionAssignment.objects.filter(
            status='active',
            end_time__lte=now
        )
        for assignment in assignments:
            assignment.status = 'expired'
            assignment.revoked_at = now
            assignment.save()
            
            # Log each expiration
            details = {
                'permission_id': assignment.permission_id,
                'status': 'expired'
            }
            if assignment.user:
                details['target_user_id'] = assignment.user_id
                user_group = f"user_{assignment.user_id}"
                async_to_sync(channel_layer.group_send)(
                    user_group,
                    {
                        'type': 'stakeholder.notification',
                        'message': f"Your permission {assignment.permission_id} has been revoked."
                    }
                )
            else:
                details['target_role'] = assignment.role
                group_name = f"{assignment.branch_id}_{assignment.role}"
                async_to_sync(channel_layer.group_send)(
                    group_name,
                    {
                        'signal': 'revoke_permission',
                        'type': 'branch.update',
                        'message': f"Your permission {assignment.permission_id} has been revoked"
                    }
                )
            log_activity.delay(None, 'expire_permission', details, assignment.branch_id, 'branch')