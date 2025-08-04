from celery import shared_task
from django.db import transaction
from .models import *
from django.contrib.auth.models import Permission
from django.contrib.auth import get_user_model
from CRE.tasks import log_activity
from CRE.models import Branch

CustomUser = get_user_model()

@shared_task
def create_permission_assignments(assignments, assigned_by_id, branch_id):
    with transaction.atomic():
        branch = Branch.objects.get(id=branch_id)
        assigned_by = CustomUser.objects.get(id=assigned_by_id)
        
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

@shared_task
def update_permission_pool(branch_id, permission_ids, created_by_id):
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